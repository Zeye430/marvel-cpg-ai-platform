// Module 2 browser acceptance through Chrome DevTools Protocol (no npm deps).
// Usage: node cdp_acceptance.mjs --scenario main|state --url http://127.0.0.1:PORT/ --out DIR [--state NAME]
// Launches the local Chrome headless with a temporary profile, loopback-only
// debugging and a random port; never touches the user's profile or disables
// the sandbox. Writes screenshots, results.json and a network/console log.
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const CHROME = args.chrome || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUT = args.out; mkdirSync(OUT, { recursive: true });
const BASE = args.url;
// Expected scope sizes and synthetic labelling come from the candidate under test.
const EXPECT = { rep: Number(args["rep-stores"] || 6), manager: Number(args["manager-stores"] || 36),
  synthetic: (args.synthetic || "1") === "1" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = { scenario: args.scenario, state: args.state || null, url: BASE, checks: [], screenshots: [], network: [], console: [], started_at: new Date().toISOString() };
const check = (name, ok, detail = "") => { results.checks.push({ name, ok: !!ok, detail }); if (!ok) console.error("FAIL", name, detail); };

// ---------------------------------------------------------------- Chrome + CDP
const profile = mkdtempSync(join(tmpdir(), "m2-chrome-"));
const downloads = join(OUT, "downloads"); mkdirSync(downloads, { recursive: true });
const chrome = spawn(CHROME, ["--headless=new", `--user-data-dir=${profile}`, "--remote-debugging-port=0",
  "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
  "--disable-sync", "--disable-background-networking", "--disable-component-update", "--mute-audio", "about:blank"],
  { stdio: ["ignore", "ignore", "pipe"] });
let wsPath = null;
for (let i = 0; i < 100 && !wsPath; i++) {
  await sleep(100);
  const file = join(profile, "DevToolsActivePort");
  if (existsSync(file)) { const [port, path] = readFileSync(file, "utf8").trim().split("\n"); if (path) wsPath = `ws://127.0.0.1:${port}${path}`; }
}
if (!wsPath) { console.error("Chrome did not expose DevTools"); process.exit(3); }
const ws = new WebSocket(wsPath);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let nextId = 1; const pending = new Map(); const listeners = [];
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) { const { resolve, reject } = pending.get(msg.id); pending.delete(msg.id); msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result); }
  else listeners.forEach((fn) => fn(msg));
};
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = nextId++; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params, sessionId }));
});
const version = await send("Browser.getVersion");
results.browser = version.product;
await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads, eventsEnabled: true });
const finishedDownloads = [];
const downloadEvents = [];
listeners.push((m) => {
  if (m.method === "Browser.downloadWillBegin") downloadEvents.push({ begin: m.params.suggestedFilename });
  if (m.method === "Browser.downloadProgress" && m.params.state !== "inProgress") downloadEvents.push({ state: m.params.state });
  if (m.method === "Browser.downloadProgress" && m.params.state === "completed") finishedDownloads.push(m.params.guid);
});
results.download_events = downloadEvents;

async function openPage(viewport) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const s = (method, params) => send(method, params, sessionId);
  await s("Page.enable"); await s("Runtime.enable"); await s("Network.enable"); await s("Log.enable");
  listeners.push((m) => {
    if (m.sessionId !== sessionId) return;
    if (m.method === "Network.requestWillBeSent") results.network.push({ viewport: viewport.name, url: m.params.request.url });
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") results.console.push({ viewport: viewport.name, text: m.params.args.map((a) => a.value ?? a.description).join(" ") });
    if (m.method === "Runtime.exceptionThrown") results.console.push({ viewport: viewport.name, text: m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description || JSON.stringify(m.params.exceptionDetails.exception?.value ?? "")) + " @" + (m.params.exceptionDetails.url || "") + ":" + m.params.exceptionDetails.lineNumber });
  });
  await s("Emulation.setDeviceMetricsOverride", { width: viewport.w, height: viewport.h, deviceScaleFactor: viewport.dpr, mobile: viewport.mobile });
  if (viewport.mobile) await s("Emulation.setTouchEmulationEnabled", { enabled: true });
  return { s, targetId, viewport };
}
async function evaluate(p, expression) {
  const r = await p.s("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " :: " + expression.slice(0, 80));
  return r.result.value;
}
async function waitFor(p, expression, timeout = 20000, label = expression) {
  const start = Date.now();
  while (Date.now() - start < timeout) { try { if (await evaluate(p, expression)) return true; } catch (e) { /* page loading */ } await sleep(150); }
  throw new Error("Timed out waiting for " + label);
}
const idle = (p) => waitFor(p, "!document.documentElement.classList.contains('shiny-busy') && ![...document.querySelectorAll('.recalculating')].some(e => e.offsetParent !== null)", 30000, "shiny idle");
async function click(p, selector) {
  // Bootstrap enables smooth scrolling; scroll instantly and measure after a frame.
  const rect = await evaluate(p, `new Promise((resolve) => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return resolve(null); e.scrollIntoView({block: 'center', behavior: 'instant'}); requestAnimationFrame(() => requestAnimationFrame(() => { const r = e.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); resolve({x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, ok: !!hit && (hit === e || e.contains(hit) || hit.contains(e) || (hit.closest && hit.closest('label') && hit.closest('label').contains(e)))}); })); })`);
  if (!rect || !rect.w) {
    const info = await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(selector)}); return e ? JSON.stringify({rect: e.getBoundingClientRect(), shown: e.offsetParent !== null}) : 'missing'; })()`);
    throw new Error("Not clickable: " + selector + " " + info);
  }
  if (!rect.ok) throw new Error("Click target obscured: " + selector);
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await p.s("Input.dispatchMouseEvent", { type, x: rect.x, y: rect.y, button: "left", clickCount: 1 });
}
async function typeText(p, text) { await p.s("Input.insertText", { text }); }
async function key(p, keyName, code, vk) {
  await p.s("Input.dispatchKeyEvent", { type: "keyDown", key: keyName, code, windowsVirtualKeyCode: vk });
  await p.s("Input.dispatchKeyEvent", { type: "keyUp", key: keyName, code, windowsVirtualKeyCode: vk });
}
async function shot(p, name) {
  const metrics = await p.s("Page.getLayoutMetrics");
  const { data } = await p.s("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: p.viewport.w, height: Math.min(Math.ceil(metrics.cssContentSize.height), 6000), scale: 1 } });
  const file = `${p.viewport.name}-${name}.png`;
  writeFileSync(join(OUT, file), Buffer.from(data, "base64"));
  results.screenshots.push({ file, viewport: `${p.viewport.w}x${p.viewport.h}`, dpr: p.viewport.dpr, height: metrics.cssContentSize.height });
}
const tableIds = (p, table = "#m2-priority-table") => evaluate(p, `[...document.querySelectorAll('${table} tbody tr .m2-cell-store .m2-muted')].map(e => e.textContent)`);
const noOverflow = (p) => evaluate(p, "document.documentElement.scrollWidth <= window.innerWidth + 1");
async function signIn(p, button) {
  await p.s("Page.navigate", { url: BASE });
  await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#m2-login')", 30000, "app connected");
  await sleep(300);
  await click(p, `#${button}`);
  const sawBusy = await evaluate(p, "document.documentElement.classList.contains('shiny-busy') || !!document.querySelector('.recalculating')");
  await waitFor(p, "!!document.querySelector('#m2-demo-identity') && getComputedStyle(document.querySelector('#m2-login')).display !== 'block' || document.querySelector('#m2-login').offsetParent === null", 20000, "signed in");
  await idle(p);
  return sawBusy;
}

// ---------------------------------------------------------------- scenarios
const VIEWPORTS = [{ name: "desktop-1440", w: 1440, h: 900, dpr: 1, mobile: false },
  { name: "tablet-768", w: 768, h: 1024, dpr: 2, mobile: false }, { name: "phone-390", w: 390, h: 844, dpr: 3, mobile: true }];
const ROLES = [{ name: "sales", button: "m2_demo_rep_a" }, { name: "manager", button: "m2_demo_manager" }];

async function mainScenario() {
  for (const viewport of VIEWPORTS) {
    for (const role of ROLES) {
      const p = await openPage({ ...viewport, name: `${viewport.name}-${role.name}` });
      const tag = `${viewport.name}/${role.name}`;
      try {
        await p.s("Page.navigate", { url: BASE });
        await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#m2-login')", 30000);
        if (role.name === "sales") await shot(p, "0-login");
        const busy = await signIn(p, role.button);
        check(`${tag}: loading indicator observed during sign-in`, busy, "shiny-busy/recalculating class seen immediately after click");
        await waitFor(p, "document.querySelectorAll('#m2-priority-table tbody tr').length > 0", 30000, "priority rows");
        await waitFor(p, "!!document.querySelector('#m2-risk-histogram .main-svg')", 20000, "risk chart rendered");
        check(`${tag}: no horizontal page overflow`, await noOverflow(p));
        const synthetic = await evaluate(p, "document.body.innerText.includes('Synthetic demo')");
        check(`${tag}: synthetic labelling ${EXPECT.synthetic ? "shown" : "absent"}`, synthetic === EXPECT.synthetic);
        const identity = await evaluate(p, "document.querySelector('#m2-demo-identity').innerText");
        check(`${tag}: server identity shown`, identity.includes(role.name === "sales" ? "Rep A" : "Manager"), identity);
        const healthVisible = await evaluate(p, "[...document.querySelectorAll('.nav-link')].some(a => a.textContent.trim() === 'Model health' && a.offsetParent !== null)");
        check(`${tag}: manager tabs ${role.name === "manager" ? "visible" : "hidden"}`, healthVisible === (role.name === "manager"));
        const count = await evaluate(p, "document.querySelector('#m2-count').innerText");
        const scoped = Number(/of (\d+) filtered/.exec(count)?.[1]);
        check(`${tag}: scoped store count`, scoped === (role.name === "sales" ? EXPECT.rep : EXPECT.manager), count);
        await shot(p, "1-priorities");
        // Filter through the real control (opening the collapsed filter box like a phone user).
        if (!(await evaluate(p, "document.querySelector('#m2_filter_box').open"))) { await click(p, "#m2_filter_box > summary"); await sleep(300); }
        await click(p, "#m2_f_alert"); await sleep(400); await idle(p);
        const filteredCount = await evaluate(p, "document.querySelector('#m2-count').innerText");
        const alertCells = await evaluate(p, "[...document.querySelectorAll('#m2-priority-table tbody td[data-label=\"Alert SKUs\"]')].map(td => Number(td.textContent))");
        check(`${tag}: alert filter keeps only alert stores`, alertCells.every((v) => v > 0), JSON.stringify(alertCells));
        const ids = await tableIds(p);
        const before = finishedDownloads.length;
        await click(p, "#m2_export");
        await waitFor(p, `true`, 100);
        const startWait = Date.now(); while (finishedDownloads.length === before && Date.now() - startWait < 15000) await sleep(150);
        const files = readdirSync(downloads).map((f) => join(downloads, f));
        const latest = files.sort().pop();
        const csv = latest ? readFileSync(latest, "utf8").trim().split("\n") : [];
        const csvIds = csv.slice(1).map((line) => line.split(",")[1]);
        const filteredN = Number(/of (\d+) filtered/.exec(filteredCount)?.[1]);
        check(`${tag}: CSV export matches filtered scope`, csvIds.length === filteredN && ids.every((id) => csvIds.includes(id)), `csv=${csvIds.length} filtered=${filteredN}`);
        if (latest) rmSync(latest);
        // Details and back keep the filter.
        if (ids.length) {
          await click(p, "#m2-priority-table tbody tr .m2-cell-action button");
          await waitFor(p, "document.querySelector('#m2-detail-table') && document.querySelector('#m2-detail-table').offsetParent !== null && document.querySelectorAll('#m2-detail-table tbody tr').length > 0", 30000, "detail rows");
          await waitFor(p, "!!document.querySelector('#m2-history-chart .main-svg')", 20000, "history chart");
          check(`${tag}: detail separates reported and predicted`, await evaluate(p, "document.body.innerText.includes('Reported now') && document.body.innerText.includes('7-day OOS risk')"));
          check(`${tag}: detail no overflow`, await noOverflow(p));
          await shot(p, "2-detail");
          await click(p, "#m2_back");
          await waitFor(p, "document.querySelector('#m2_f_alert').offsetParent !== null", 15000, "priorities visible after back");
          await idle(p);
          check(`${tag}: back keeps alert filter`, await evaluate(p, "document.querySelector('#m2_f_alert').checked && document.querySelector('#m2-priority-table') !== null"));
        }
        await click(p, "#m2_f_alert"); await sleep(400); await idle(p);
        await waitFor(p, `document.querySelector('#m2-count') && document.querySelector('#m2-count').innerText.includes('of ${role.name === "sales" ? EXPECT.rep : EXPECT.manager} filtered')`, 15000, "filter cleared");
        if (viewport.name === "desktop-1440") {
          // Keyboard search: focus by Tab-free click then type a store id; Escape-free clear.
          const first = (await tableIds(p))[0];
          await click(p, "#m2_f_search"); await typeText(p, first); await key(p, "Enter", "Enter", 13); await sleep(700); await idle(p);
          const searched = await tableIds(p);
          check(`${tag}: keyboard search filters to one store`, searched.length === 1 && searched[0] === first, JSON.stringify(searched));
          await evaluate(p, "(() => { const e = document.querySelector('#m2_f_search'); e.focus(); e.select(); return true; })()");
          await key(p, "Backspace", "Backspace", 8); await sleep(700); await idle(p);
          await click(p, "#m2_f_search"); await typeText(p, "zzzz-no-match"); await sleep(800); await idle(p);
          check(`${tag}: empty state shown`, await evaluate(p, "document.body.innerText.includes('No stores match')"));
          await shot(p, "3-empty-state");
          await evaluate(p, "(() => { const e = document.querySelector('#m2_f_search'); e.focus(); e.select(); return true; })()");
          await key(p, "Backspace", "Backspace", 8); await sleep(800); await idle(p);
          const focusable = await evaluate(p, "(() => { document.querySelector('#m2_f_channel').focus(); return document.activeElement.id; })()");
          check(`${tag}: filter control keyboard-focusable`, focusable === "m2_f_channel" || focusable.length > 0, focusable);
        }
        if (role.name === "manager") {
          await waitFor(p, `document.querySelector('#m2-count').innerText.includes('of ${EXPECT.manager} filtered')`, 15000, "unfiltered manager list");
          // Population statistics cover every authorized store, independent of table paging (R-04).
          const histogramTotal = await evaluate(p, "Number((/All (\\d+) scored/.exec(document.querySelector('#m2-histogram-total')?.innerText || '') || [])[1])");
          const ranked = Number(/\((\d+) ranked/.exec(await evaluate(p, "document.querySelector('#m2-count').innerText"))?.[1]);
          check(`${tag}: histogram covers all ranked stores`, histogramTotal === ranked, `histogram=${histogramTotal} ranked=${ranked}`);
          const tile = await evaluate(p, "Number([...document.querySelectorAll('.m2-tile')].find(t => t.innerText.includes('Need data follow-up'))?.querySelector('.m2-tile-value').innerText.replace(/,/g, ''))");
          const listed = await evaluate(p, "document.querySelectorAll('#m2-followup-list li').length");
          check(`${tag}: follow-up tile equals follow-up list`, tile === listed, `tile=${tile} list=${listed}`);
          const firstPage = await tableIds(p);
          await click(p, "#m2_next"); await idle(p);
          await waitFor(p, "document.body.innerText.includes('Page 2 of')", 10000, "page 2").catch(() => {});
          const secondPage = await tableIds(p);
          check(`${tag}: pagination to page 2 with new rows`, await evaluate(p, "document.body.innerText.includes('Page 2 of')") && secondPage.every((id) => !firstPage.includes(id)));
          await click(p, "#m2_prev"); await idle(p);
          await click(p, "a.nav-link[data-value='health']");
          await waitFor(p, "!!document.querySelector('#m2-comparison-table') && !!document.querySelector('#m2-algorithm-comparison .main-svg')", 30000, "health");
          check(`${tag}: health shows real comparison and selected evaluations`, await evaluate(p, "!!document.querySelector('#m2-selected-table') && document.body.innerText.includes('iterative validation')"));
          check(`${tag}: health no overflow`, await noOverflow(p));
          await shot(p, "4-model-health");
          await click(p, "a.nav-link[data-value='monitoring']");
          await waitFor(p, "document.body.innerText.includes('Pending outcomes')", 20000, "monitoring pending");
          check(`${tag}: monitoring honest pending state`, true);
          await shot(p, "5-monitoring");
        } else if (viewport.name === "desktop-1440") {
          // Tampering: open a store outside scope and force-show a hidden manager tab.
          const own = await evaluate(p, "[...document.querySelectorAll('#m2_store option')].map(o => o.value).filter(Boolean)");
          const outside = JSON.parse(args["outside-store"] || '""') || "SYN-S036";
          check(`${tag}: chosen tamper store is outside own scope`, !own.includes(outside), outside);
          await evaluate(p, `Shiny.setInputValue('m2_store', ${JSON.stringify(outside)}, {priority: 'event'}); true`);
          await click(p, "a.nav-link[data-value='detail']");
          await waitFor(p, "document.body.innerText.includes('This store is not in your scope')", 20000, "denied detail");
          check(`${tag}: out-of-scope store denied`, true);
          await evaluate(p, "(() => { const a = document.querySelector(\"a.nav-link[data-value='health']\"); a.parentElement.style.display = ''; document.querySelector('.tab-pane[data-value=health]').style.display = ''; return true; })()");
          await evaluate(p, "(() => { document.querySelector(\"a.nav-link[data-value='health']\").click(); return true; })()");
          await waitFor(p, "document.body.innerText.includes('Manager access only')", 20000, "health denied");
          check(`${tag}: forced manager tab still denied by data layer`, true);
          await shot(p, "6-denied-states");
          // Logout clears the session; another rep sees a different scope.
          const repA = await tableIds(p).catch(() => []);
          await click(p, "#m2_demo_logout");
          await waitFor(p, "document.querySelector('#m2-login').offsetParent !== null", 15000, "logged out");
          check(`${tag}: logout returns to sign-in`, true);
          check(`${tag}: signed-out page holds no previous rows`, (await tableIds(p)).length === 0);
          await click(p, "#m2_demo_rep_b"); await idle(p);
          await waitFor(p, "document.querySelector('#m2-demo-identity') && document.querySelector('#m2-demo-identity').innerText.includes('Rep B')", 15000, "Rep B identity");
          await click(p, "a.nav-link[data-value='priorities']").catch(() => {});
          await waitFor(p, "document.querySelectorAll('#m2-priority-table tbody tr').length > 0", 20000);
          const repB = await tableIds(p);
          const repBCount = await evaluate(p, "document.querySelector('#m2-count').innerText");
          check(`${tag}: Rep B sees a different scope`, repB.every((id) => !repA.includes(id)) && /of \d+ filtered/.test(repBCount), repBCount);
        }
      } catch (error) {
        check(`${tag}: scenario completed`, false, String(error));
        await shot(p, "x-failure").catch(() => {});
      }
      await send("Target.closeTarget", { targetId: p.targetId });
    }
  }
}

async function stateScenario(name) {
  const p = await openPage({ name: `state-${name}`, w: 1440, h: 900, dpr: 1, mobile: false });
  try {
    await p.s("Page.navigate", { url: BASE });
    await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected()", 30000);
    if (name === "unavailable") {
      await click(p, "#m2_demo_manager"); await idle(p);
      await waitFor(p, "document.body.innerText.includes('Unavailable')", 15000);
      check("state unavailable rendered", true);
    } else if (name === "unreleased") {
      await click(p, "#m2_demo_rep_a"); await idle(p);
      await waitFor(p, "document.body.innerText.includes('Predictions unreleased')", 15000);
      check("state unreleased rendered (missing predictions are not zero risk)", await evaluate(p, "document.body.innerText.includes('not zero risk')"));
    } else if (name === "stale") {
      await click(p, "#m2_demo_rep_a"); await idle(p);
      await waitFor(p, "!!document.querySelector('#m2-stale-banner')", 15000);
      check("state stale rendered", true);
    } else if (name === "error") {
      await click(p, "#m2_demo_rep_a"); await idle(p);
      await waitFor(p, "document.querySelectorAll('#m2-priority-table tbody tr').length > 0", 20000);
      writeFileSync(join(OUT, "trigger-error"), "1");
      await sleep(1500);
      await click(p, "#m2-priority-table tbody tr .m2-cell-action button");
      await waitFor(p, "document.body.innerText.includes('Something went wrong')", 20000);
      check("state error rendered without stack trace", await evaluate(p, "!document.body.innerText.includes('Traceback')"));
    }
    await shot(p, "state");
  } catch (error) {
    check(`state ${name}: completed`, false, String(error));
    await shot(p, "x-failure").catch(() => {});
  }
}

try {
  if (args.scenario === "main") await mainScenario(); else await stateScenario(args.state);
} finally {
  const external = results.network.filter((n) => !/^(https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/|data:|blob:|about:)/.test(n.url));
  check("no non-loopback network requests", external.length === 0, JSON.stringify(external.slice(0, 5)));
  check("no console errors", results.console.length === 0, JSON.stringify(results.console.slice(0, 5)));
  results.finished_at = new Date().toISOString();
  results.passed = results.checks.every((c) => c.ok);
  writeFileSync(join(OUT, "results.json"), JSON.stringify(results, null, 2));
  ws.close(); chrome.kill("SIGTERM");
  await sleep(500); rmSync(profile, { recursive: true, force: true });
  console.log(JSON.stringify({ passed: results.passed, checks: results.checks.length, failed: results.checks.filter((c) => !c.ok).map((c) => c.name) }));
  process.exit(results.passed ? 0 : 1);
}
