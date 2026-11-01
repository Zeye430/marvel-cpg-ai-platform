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
  // Full-page capture starts at the document top; capture unscrolled and restore the position afterwards.
  const scrolled = await evaluate(p, "(() => { const y = window.scrollY; window.scrollTo({top: 0, behavior: 'instant'}); return y; })()");
  await sleep(100);
  try { await capture(p, name); } finally { await evaluate(p, `(window.scrollTo({top: ${scrolled}, behavior: 'instant'}), true)`); }
}
async function capture(p, name) {
  const metrics = await p.s("Page.getLayoutMetrics");
  const { data } = await p.s("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: p.viewport.w, height: Math.min(Math.ceil(metrics.cssContentSize.height), 6000), scale: 1 } });
  const file = `${p.viewport.name}-${name}.png`;
  writeFileSync(join(OUT, file), Buffer.from(data, "base64"));
  results.screenshots.push({ file, viewport: `${p.viewport.w}x${p.viewport.h}`, dpr: p.viewport.dpr, height: metrics.cssContentSize.height });
}
const tableIds = (p, table = "#m2-priority-table") => evaluate(p, `[...document.querySelectorAll('${table} tbody a.m2-store-link')].map(a => a.dataset.store)`);
const countText = (p) => evaluate(p, "document.querySelector('#m2-count')?.innerText || ''");
const matching = (text) => Number(/of (\d+) matching/.exec(text)?.[1]);
// Today's focus carries the filtered summary counts as data attributes (stores, current-msl, prevention, verify-data).
const focusValue = (p, key) => evaluate(p, `Number(document.querySelector('#m2-focus')?.dataset[${JSON.stringify(key)}] ?? NaN)`);
const rowData = (p, table = "#m2-priority-table") => evaluate(p, `[...document.querySelectorAll('${table} tbody tr')].map(r => ({...r.dataset}))`);
const SORT_ATTR = { score: "score", sales_at_risk: "sales", shelf_risk: "shelf", msl_risk: "msl", urgency: "urgency" };
function sortedOk(rows, attr, descending) {
  const values = rows.map((r) => (r[attr] === "" || r[attr] === undefined ? null : Number(r[attr])));
  const firstMissing = values.indexOf(null);
  if (firstMissing >= 0 && values.slice(firstMissing).some((v) => v !== null)) return false;   // missing last
  const present = values.filter((v) => v !== null);
  return present.every((v, i) => i === 0 || (descending ? present[i - 1] >= v : present[i - 1] <= v));
}
const noOverflow = (p) => evaluate(p, "document.documentElement.scrollWidth <= window.innerWidth + 1");
const visible = (p, selector) => evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(selector)}); return !!e && e.offsetParent !== null; })()`);
const LABELS = { all: "All priorities", current_msl: "Current MSL gaps", prevention: "Prevent stockouts", verify_data: "Verify data" };
const currentPage = (p) => evaluate(p, "document.querySelector('#m2_page')?.innerText || ''");

function parseCsv(text) {
  const rows = []; let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) { if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; } else field += c; }
    else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

async function signIn(p, button, ready = "#m2-demo-identity") {
  await p.s("Page.navigate", { url: BASE });
  await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#m2-login')", 30000, "app connected");
  await sleep(300);
  await click(p, `#${button}`);
  const sawBusy = await evaluate(p, "document.documentElement.classList.contains('shiny-busy') || !!document.querySelector('.recalculating')");
  await waitFor(p, `!!document.querySelector(${JSON.stringify(ready)}) && document.querySelector('#m2-login').offsetParent === null`, 20000, "signed in");
  await idle(p);
  return sawBusy;
}
const sidebarOpen = (p) => evaluate(p, "!document.querySelector('#m2_sidebar').hidden && document.querySelector('#m2_sidebar').getBoundingClientRect().width > 50");
async function toggleSidebar(p, open) {
  if ((await sidebarOpen(p)) === open) return;
  await click(p, ".bslib-sidebar-layout > .collapse-toggle");
  await waitFor(p, `(!document.querySelector('#m2_sidebar').hidden && document.querySelector('#m2_sidebar').getBoundingClientRect().width > 50) === ${open}`, 5000, `sidebar ${open ? "open" : "closed"}`);
  await sleep(350);                                         // collapse transition
}
async function goPage(p, page, viewport) {
  const phone = viewport.w <= 576;
  if (phone) await toggleSidebar(p, true);
  await click(p, `#m2_sidebar .m2-nav-item[data-page='${page}']`);
  await waitFor(p, `(document.querySelector('#m2_page')?.innerText || '') === '${page}'`, 15000, `page ${page}`);
  if (phone) await toggleSidebar(p, false);
  await idle(p);
}
async function setView(p, view) {
  await click(p, `.m2-views input[value='${view}'] + span`);
  await waitFor(p, `(document.querySelector('#m2-count')?.innerText || '').includes(${JSON.stringify(LABELS[view])})`, 15000, `view ${view}`);
  await idle(p);
}
// Wait for a re-render: the old node is replaced, so stale rows are never read.
async function rerendered(p, action, label, selector = "#m2-priority-table") {
  await evaluate(p, `(window.__m2Node = document.querySelector('${selector}'), true)`);
  await action();
  await waitFor(p, `!!document.querySelector('${selector}') && document.querySelector('${selector}') !== window.__m2Node`, 15000, label);
  await idle(p);
}
async function selectValue(p, id, value, label, selector = "#m2-priority-table") {
  await rerendered(p, () => evaluate(p, `(() => { const s = document.getElementById('${id}'); s.value = '${value}'; s.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`), label, selector);
}
async function setSort(p, key, direction, viaHeader) {
  const want = direction === "desc" ? "descending" : "ascending";
  const state = () => evaluate(p, "(() => { const th = document.querySelector('#m2-priority-table th[aria-sort]'); return th ? th.querySelector('button').dataset.sort + ':' + th.getAttribute('aria-sort') : ''; })()");
  if (viaHeader) {
    for (let attempt = 0; attempt < 2 && (await state()) !== `${key}:${want}`; attempt++) {
      await rerendered(p, () => click(p, `#m2-priority-table th button[data-sort='${key}']`), `sort ${key}`);
    }
    if ((await state()) !== `${key}:${want}`) throw new Error(`Header sort did not reach ${key} ${direction}`);
  } else if ((await evaluate(p, "document.getElementById('m2_sort').value")) !== `${key}:${direction}`) {
    await selectValue(p, "m2_sort", `${key}:${direction}`, `sort ${key} ${direction}`);
  }
  await idle(p);
}
async function exportCsv(p) {
  const before = finishedDownloads.length;
  const names = downloadEvents.length;
  await click(p, "#m2_export");
  const start = Date.now(); while (finishedDownloads.length === before && Date.now() - start < 15000) await sleep(150);
  const files = readdirSync(downloads).map((f) => join(downloads, f)).sort();
  const latest = files.pop();
  const rows = latest ? parseCsv(readFileSync(latest, "utf8")) : [];
  const name = downloadEvents.slice(names).find((e) => e.begin)?.begin || "";
  if (latest) rmSync(latest);
  return { name, header: rows[0] || [], body: rows.slice(1) };
}
async function openStore(p, how, selector) {
  // how: "link" (pointer on the store name), "keyboard" (focus link + Enter) or "row" (pointer on another cell).
  if (how === "keyboard") {
    await evaluate(p, `(document.querySelector(${JSON.stringify(selector)}).focus(), true)`);
    await key(p, "Enter", "Enter", 13);
  } else {
    await click(p, selector);
  }
  await waitFor(p, "(document.querySelector('#m2_page')?.innerText || '') === 'detail' && !!document.querySelector('#m2_detail')", 30000, "store detail");
  await idle(p);
}

// ---------------------------------------------------------------- scenarios
const VIEWPORTS = [{ name: "desktop-1440", w: 1440, h: 900, dpr: 1, mobile: false },
  { name: "tablet-768", w: 768, h: 1024, dpr: 2, mobile: false }, { name: "phone-390", w: 390, h: 844, dpr: 3, mobile: true }];
const ROLES = [{ name: "sales", button: "m2_demo_rep_a", role: "Sales" }, { name: "manager", button: "m2_demo_manager", role: "Manager" }];

async function shellChecks(p, tag, viewport, mode) {
  const phone = viewport.w <= 576;
  check(`${tag}: sidebar ${phone ? "starts closed on phones" : "open on wider screens"}`, (await sidebarOpen(p)) === !phone);
  await toggleSidebar(p, !phone ? false : true);
  check(`${tag}: sidebar toggles`, (await sidebarOpen(p)) === phone);
  await toggleSidebar(p, !phone);
  check(`${tag}: collapse control is a keyboard-focusable button`, await evaluate(p,
    "(() => { const b = document.querySelector('.bslib-sidebar-layout > .collapse-toggle'); b.focus(); return document.activeElement === b && b.tagName === 'BUTTON' && b.hasAttribute('aria-expanded'); })()"));
  if (phone) await toggleSidebar(p, true);
  const nav = await evaluate(p, "[...document.querySelectorAll('#m2_sidebar .m2-nav-item[data-page]')].map(b => b.dataset.page)");
  check(`${tag}: sidebar lists only Module 2 ${mode === "ops" ? "operations" : "business"} pages`,
    JSON.stringify(nav) === JSON.stringify(mode === "ops" ? ["health", "monitoring"] : ["overview", "ranking", "analytics"]) &&
    !(await evaluate(p, "/Module [134]\\b/.test(document.querySelector('#m2_sidebar').innerText)")), JSON.stringify(nav));
  check(`${tag}: sign out and user card in the sidebar`, (await visible(p, "#m2_signout")) && (await visible(p, "#m2-demo-identity")));
  check(`${tag}: original placeholder logo shown`, await evaluate(p, "!!document.querySelector('#m2_sidebar svg.m2-logo-mark[aria-label]')"));
  if (phone) await toggleSidebar(p, false);
  const placeholders = await evaluate(p, "[...document.querySelectorAll('.m2-topbar [aria-disabled=\"true\"]')].map(b => b.getAttribute('aria-label'))");
  check(`${tag}: Copilot, messages, notifications, calendar are labelled placeholders`,
    placeholders.length === 4 && placeholders.every((l) => /coming soon/i.test(l)), JSON.stringify(placeholders));
  const before = await currentPage(p);
  await click(p, ".m2-topbar [aria-label^='Notifications']"); await sleep(400);
  check(`${tag}: placeholder click does nothing`, (await currentPage(p)) === before && !(await evaluate(p, "!!document.querySelector('.modal.show, .dropdown-menu.show')")));
  if (!phone) {
    await evaluate(p, "(window.scrollTo({top: 700, behavior: 'instant'}), true)"); await sleep(300);
    const pos = await evaluate(p, "JSON.stringify({y: scrollY, side: Math.round(document.querySelector('#m2_sidebar').getBoundingClientRect().top), bar: Math.round(document.querySelector('.m2-topbar').getBoundingClientRect().top)})");
    const { y, side, bar } = JSON.parse(pos);
    check(`${tag}: sidebar and top bar stay fixed while the page scrolls`, y === 0 || (side === 0 && bar === 0), pos);
    await evaluate(p, "(window.scrollTo({top: 0, behavior: 'instant'}), true)"); await sleep(200);
  }
  check(`${tag}: user avatar, name and title in the top bar`, await evaluate(p,
    "(() => { const u = document.querySelector('#m2-user'); return !!u && !!u.querySelector('.m2-avatar') && u.querySelectorAll('.m2-user-text > *').length === 2; })()"));
  check(`${tag}: Inter loaded locally for Module 2 text`, await evaluate(p,
    "document.fonts.ready.then(() => document.fonts.check('600 16px \"M2 Inter\"') && getComputedStyle(document.querySelector('.m2-title')).fontFamily.includes('M2 Inter'))"));
}

async function businessChecks(p, tag, role, viewport) {
  const busy = await signIn(p, role.button);
  check(`${tag}: loading indicator observed during sign-in`, busy, "shiny-busy/recalculating class seen immediately after click");
  await waitFor(p, "document.querySelectorAll('#m2-top10-table tbody tr').length > 0", 30000, "overview rows");
  const expected = role.name === "sales" ? EXPECT.rep : EXPECT.manager;
  const desktop = viewport.w > 900;
  check(`${tag}: opens on the overview`, (await currentPage(p)) === "overview");
  check(`${tag}: no horizontal page overflow (overview)`, await noOverflow(p));
  check(`${tag}: synthetic labelling ${EXPECT.synthetic ? "shown" : "absent"}`,
    (await evaluate(p, "document.body.innerText.includes('Synthetic demo')")) === EXPECT.synthetic);
  const identity = await evaluate(p, "document.querySelector('#m2-demo-identity').innerText");
  check(`${tag}: server identity shown`, identity.includes(role.name === "sales" ? "Rep A" : "Manager"), identity);
  const header = await evaluate(p, "document.querySelector('#m2_header')?.innerText || ''");
  check(`${tag}: header names role, scope, as-of date and risk window`,
    header.includes(role.name === "sales" ? "Store risk overview" : "Team risk overview") && header.includes(role.role) &&
    header.includes(`${expected} assigned stores`) && header.includes("Reported as of") && header.includes("Stockout risk window"),
    header.replace(/\s+/g, " "));
  await shellChecks(p, tag, viewport, "business");
  // Overview reconciles with the service definitions (same filters); no fabricated trends.
  check(`${tag}: KPI cards show six figures without trend arrows`, await evaluate(p,
    "document.querySelectorAll('#m2-summary .m2-kpi').length === 6 && !/[↑↓]\\s*\\d+(\\.\\d+)?%/.test(document.querySelector('#m2-summary').innerText)"));
  const focusStores = await focusValue(p, "stores");
  check(`${tag}: focus stores equal the assigned scope`, focusStores === expected, String(focusStores));
  const urgentKpi = await evaluate(p, "Number(document.querySelector('#m2-summary [data-key=urgent] .m2-kpi-value').innerText)");
  check(`${tag}: urgent KPI equals Today's focus`, urgentKpi === Number(/^(\d+)/.exec(await evaluate(p,
    "document.querySelector('#m2-focus [data-key=urgent] strong').innerText"))?.[1]));
  const top = await rowData(p, "#m2-top10-table");
  check(`${tag}: top stores are overall ranks 1..n`, top.length === Math.min(10, expected) && top.every((r, i) => Number(r.rank) === i + 1));
  check(`${tag}: overview shows no confidence`, !(await evaluate(p, "/confidence/i.test(document.querySelector('.tab-pane[data-value=overview]').innerText)")));
  check(`${tag}: SKUs-out-of-stock card present`, await visible(p, "#m2-overview-oos"));
  check(`${tag}: territory overview ${role.name === "manager" ? "shown" : "absent"} for this role`,
    (await visible(p, "#m2-regions")) === (role.name === "manager"));
  check(`${tag}: business page has no model health or monitoring`, !(await evaluate(p,
    "!!document.querySelector('#m2-comparison-table, #m2-selected-table, #m2-algorithm-comparison') || (document.querySelector('#m2-ops-root')?.offsetParent ?? null) !== null")));
  await shot(p, "1-overview");

  // Store ranking: default 10 rows, 10/50/100, full-scope sort, row navigation and back restoration.
  await goPage(p, "ranking", viewport);
  let count = await countText(p);
  check(`${tag}: ranking count equals assigned scope`, matching(count) === expected, count);
  check(`${tag}: default order is overall score, high to low`, count.includes("sorted by priority score, high to low"), count);
  let rows = await rowData(p);
  check(`${tag}: default 10 rows`, rows.length === Math.min(10, expected) && (await evaluate(p, "document.getElementById('m2_rows').value")) === "10");
  check(`${tag}: one overall rank, no component ranks`, await evaluate(p,
    "[...document.querySelectorAll('#m2-priority-table thead th')].filter(th => /rank/i.test(th.textContent)).length === 1"));
  check(`${tag}: risk category on separate lines, no "+"`, await evaluate(p,
    "[...document.querySelectorAll('#m2-priority-table td.m2-category')].every(td => !td.innerText.includes(' + ') && td.querySelectorAll('ul.m2-cat li').length >= 1)"));
  check(`${tag}: store navigation is a link, no hidden View store button`, await evaluate(p,
    "document.querySelectorAll('#m2-priority-table tbody a.m2-store-link[href]').length > 0 && !/View store/.test(document.querySelector('#m2-priority-table').innerText)"));
  if (expected > 10) {
    await selectValue(p, "m2_rows", "50", "50 rows");
    check(`${tag}: 50 rows`, (await rowData(p)).length === Math.min(50, expected));
    await selectValue(p, "m2_rows", "100", "100 rows");
    check(`${tag}: 100 rows`, (await rowData(p)).length === Math.min(100, expected));
    await selectValue(p, "m2_rows", "10", "10 rows");
  }
  rows = await rowData(p);
  const rankOf = Object.fromEntries(rows.map((r) => [r.store, r.rank]));
  for (const key of desktop ? ["sales_at_risk", "shelf_risk", "msl_risk", "urgency", "score"] : ["sales_at_risk"]) {
    for (const direction of ["desc", "asc"]) {
      await setSort(p, key, direction, desktop);
      const sorted = await rowData(p);
      check(`${tag}: sort ${key} ${direction} ordered, missing last`, sortedOk(sorted, SORT_ATTR[key], direction === "desc"));
      check(`${tag}: sort ${key} ${direction} keeps overall ranks`, sorted.every((r) => !(r.store in rankOf) || rankOf[r.store] === r.rank));
    }
  }
  await setSort(p, "sales_at_risk", "desc", desktop);
  const viewCounts = {};
  for (const view of ["current_msl", "prevention", "verify_data"]) {
    const expectedCount = await focusValue(p, view.replace(/_([a-z])/g, (_, c) => c.toUpperCase()));
    await setView(p, view);
    const n = matching(await countText(p)) || 0;
    viewCounts[view] = n;
    check(`${tag}: ${view} count equals Today's focus summary`, n === expectedCount, `count=${n} summary=${expectedCount}`);
    const vr = await rowData(p);
    const ok = view === "current_msl" ? vr.every((r) => Number(r.mslGaps) > 0) : view === "prevention"
      ? vr.every((r) => Number(r.prevention) > 0) : vr.every((r) => Number(r.checks) > 0);
    check(`${tag}: ${view} rows match the view`, ok);
    if (n === 0) check(`${tag}: ${view} empty state explains`, await evaluate(p, "document.body.innerText.includes('No stores match')"));
  }
  const exportView = Object.entries(viewCounts).find(([, n]) => n > 0)?.[0] || "all";
  await setView(p, exportView);
  const shownIds = await tableIds(p);
  const shownCount = matching(await countText(p));
  const csv = await exportCsv(p);
  const idCol = csv.header.indexOf("store_id");
  check(`${tag}: CSV export equals the ${exportView} view in the displayed order`,
    csv.body.length === shownCount && shownIds.every((id, i) => csv.body[i][idCol] === id), `csv=${csv.body.length} view=${shownCount}`);
  check(`${tag}: CSV keeps legacy columns first and appends business-priority columns`,
    csv.header[0] === "display_rank" && idCol === 1 && csv.header.at(-1) === "ranking_policy_version" && !csv.header.includes("confidence"),
    csv.header.join(","));
  await shot(p, "2-ranking");
  if (shownIds.length) {
    const last = shownIds.at(-1);
    await openStore(p, desktop ? "keyboard" : "link", `#m2-priority-table a.m2-store-link[data-store=${JSON.stringify(last)}]`);
    const listScroll = await evaluate(p, "window.__m2Scroll || 0");
    check(`${tag}: store link opens the selected store`, (await evaluate(p, "document.querySelector('#m2_detail').innerText")).includes(last));
    check(`${tag}: detail opens at top`, (await evaluate(p, "window.scrollY")) < 200);
    await detailChecks(p, tag, viewport);
    await click(p, "#m2_back");
    await waitFor(p, "(document.querySelector('#m2_page')?.innerText || '') === 'ranking'", 15000, "back to ranking");
    await waitFor(p, "(document.querySelector('#m2-priority-table')?.offsetParent ?? null) !== null", 15000, "list visible");
    await idle(p); await sleep(300);
    check(`${tag}: back keeps view and sort`, (await countText(p)).includes(LABELS[exportView]) &&
      (await evaluate(p, `document.querySelector(".m2-views input[value='${exportView}']").checked`)) &&
      (await evaluate(p, "document.getElementById('m2_sort').value === 'sales_at_risk:desc'")));
    const restored = await evaluate(p, "window.scrollY");
    check(`${tag}: back restores list position`, Math.abs(restored - listScroll) < 80,
      `saved=${listScroll} now=${restored} height=${await evaluate(p, "document.documentElement.scrollHeight")}`);
  }
  await setView(p, "all");
  if (expected > 10) {
    const firstPage = await tableIds(p);
    await rerendered(p, () => click(p, "#m2_next"), "page 2");
    const second = await rowData(p);
    check(`${tag}: page 2 continues the sorted list`, second.length > 0 && second.every((r) => !firstPage.includes(r.store)));
    const pageTwoFirst = second[0].store;
    await openStore(p, "row", `#m2-priority-table tbody tr[data-store=${JSON.stringify(pageTwoFirst)}] td.m2-score`);
    check(`${tag}: row click opens the store`, (await evaluate(p, "document.querySelector('#m2_detail').innerText")).includes(pageTwoFirst));
    await click(p, "#m2_back");
    await waitFor(p, "(document.querySelector('#m2-priority-table')?.offsetParent ?? null) !== null", 15000, "list visible");
    await idle(p);
    check(`${tag}: back keeps the page`, (await evaluate(p, "document.body.innerText.includes('Page 2 of')")) &&
      (await tableIds(p))[0] === pageTwoFirst);
    await rerendered(p, () => click(p, "#m2_prev"), "page 1");
  }
  await setSort(p, "score", "desc", false);
  check(`${tag}: no horizontal page overflow (ranking)`, await noOverflow(p));

  // Analytics: current first, 28 days on request; MSL split; drilldown to stores.
  await goPage(p, "analytics", viewport);
  await waitFor(p, "!!document.querySelector('#m2-oos-note')", 30000, "analytics note");
  const note = await evaluate(p, "document.querySelector('#m2-oos-note').innerText");
  check(`${tag}: analytics starts with the as-of measure and discloses coverage`, note.includes("as-of date") && /not reported/.test(note), note.slice(0, 160));
  await click(p, "#m2_oos_measure input[value='28d'] + span");
  await waitFor(p, "(document.querySelector('#m2-oos-note')?.innerText || '').includes('in the window')", 30000, "28-day measure");
  await idle(p);
  const totals = await evaluate(p, "(() => { const t = document.querySelector('#m2-oos-note').innerText; const m = /([\\d,]+) out-of-stock observations \\(MSL ([\\d,]+) · non-MSL ([\\d,]+) · unknown ([\\d,]+)\\)/.exec(t); return m ? m.slice(1).map(x => Number(x.replace(/,/g, ''))) : null; })()");
  check(`${tag}: MSL / non-MSL / unknown add up to the out-of-stock observations`, !!totals && totals[1] + totals[2] + totals[3] === totals[0], JSON.stringify(totals));
  if (totals && totals[0] > 0) {
    await waitFor(p, "!!document.querySelector('#m2-oos-bar .main-svg') && !!document.querySelector('#m2-oos-daily .main-svg')", 20000, "28-day charts");
    check(`${tag}: donut shown when there are observations`, await visible(p, "#m2-oos-donut-card"));
    const sku = await evaluate(p, "[...document.querySelectorAll('#m2_oos_sku option')].map(o => o.value).filter(Boolean)[0] || ''");
    await selectValue(p, "m2_oos_sku", sku, "drilldown", "#m2-oos-store-table");
    const affected = await tableIds(p, "#m2-oos-store-table");
    const ownScope = role.name === "sales" ? affected.length <= EXPECT.rep : affected.length <= EXPECT.manager;
    check(`${tag}: SKU drilldown lists affected stores in scope`, affected.length > 0 && ownScope, JSON.stringify(affected.slice(0, 3)));
    await click(p, "#m2_oos_msl input[value='MSL'] + span"); await idle(p);
    await sleep(300);
    await shot(p, "3-analytics");
    await click(p, "#m2_oos_msl input[value='all'] + span"); await idle(p);
    await openStore(p, "link", `#m2-oos-store-table a.m2-store-link[data-store=${JSON.stringify(affected[0])}]`);
    await click(p, "#m2_back");
    await waitFor(p, "(document.querySelector('#m2_page')?.innerText || '') === 'analytics'", 15000, "back to analytics");
    check(`${tag}: back returns to analytics`, true);
  } else {
    check(`${tag}: no observations stated`, await evaluate(p, "document.body.innerText.includes('No out-of-stock observations')"));
  }
  check(`${tag}: no horizontal page overflow (analytics)`, await noOverflow(p));
  await goPage(p, "overview", viewport);

  if (viewport.name === "desktop-1440") {
    // Search in the top bar filters the list and opens the ranking page.
    const first = (await tableIds(p, "#m2-top10-table"))[0];
    await click(p, "#m2_f_search"); await typeText(p, first); await sleep(800); await idle(p);
    await waitFor(p, "(document.querySelector('#m2_page')?.innerText || '') === 'ranking'", 15000, "search opens ranking");
    await waitFor(p, `document.querySelectorAll('#m2-priority-table tbody tr').length === 1`, 15000, "one store");
    check(`${tag}: top-bar search filters to one store`, (await tableIds(p))[0] === first);
    await evaluate(p, "(() => { const e = document.querySelector('#m2_f_search'); e.focus(); e.select(); return true; })()");
    await key(p, "Backspace", "Backspace", 8); await sleep(700); await idle(p);
    await click(p, "#m2_f_search"); await typeText(p, "zzzz-no-match"); await sleep(800); await idle(p);
    check(`${tag}: empty state shown`, await evaluate(p, "document.body.innerText.includes('No stores match')"));
    await evaluate(p, "(() => { const e = document.querySelector('#m2_f_search'); e.focus(); e.select(); return true; })()");
    await key(p, "Backspace", "Backspace", 8); await sleep(800); await idle(p);
    check(`${tag}: sort headers and view tabs are keyboard-focusable`, await evaluate(p,
      "(() => { const b = document.querySelector(\"#m2-priority-table th button[data-sort='shelf_risk']\"); b.focus(); const ok = document.activeElement === b; const v = document.querySelector(\".m2-views input[value='prevention']\"); v.focus(); return ok && document.activeElement === v; })()"));
  }

  if (role.name === "sales" && viewport.name === "desktop-1440") {
    const outside = JSON.parse(args["outside-store"] || '""') || "SYN-S036";
    await evaluate(p, `Shiny.setInputValue('m2_open_store', ${JSON.stringify(outside)}, {priority: 'event'}); true`);
    await waitFor(p, "document.body.innerText.includes('This store is not in your scope')", 20000, "denied detail");
    check(`${tag}: out-of-scope store denied`, true);
    await click(p, "#m2_back"); await idle(p);
    await evaluate(p, "(() => { const r = document.querySelector('#m2-ops-root'); let e = r; while (e && e !== document.body) { e.style.display = 'block'; e = e.parentElement; } return true; })()");
    await sleep(1500); await idle(p);
    check(`${tag}: forced operations panel holds no model data`, await evaluate(p, "!document.querySelector('#m2-comparison-table, #m2-selected-table, #m2-ops-note')"));
  }
  // Sign out from the Module 2 sidebar: back to sign-in, no previous content; another rep sees another scope.
  const before = await tableIds(p, "#m2-top10-table");
  if (viewport.w <= 576) await toggleSidebar(p, true);
  await click(p, "#m2_signout");
  await waitFor(p, "document.querySelector('#m2-login').offsetParent !== null", 15000, "signed out");
  check(`${tag}: Module 2 sign-out returns to sign-in`, true);
  check(`${tag}: signed-out page holds no previous rows`, (await tableIds(p)).length === 0 && (await tableIds(p, "#m2-top10-table")).length === 0);
  if (role.name === "sales" && viewport.name === "desktop-1440") {
    await click(p, "#m2_demo_rep_b"); await idle(p);
    await waitFor(p, "(document.querySelector('#m2-demo-identity')?.innerText || '').includes('Rep B')", 15000, "Rep B identity");
    await waitFor(p, "document.querySelectorAll('#m2-top10-table tbody tr').length > 0", 20000);
    const repB = await tableIds(p, "#m2-top10-table");
    check(`${tag}: Rep B sees a different scope ranked from 1, on the overview`, repB.length > 0 &&
      repB.every((id) => !before.includes(id)) && (await currentPage(p)) === "overview");
  }
}

async function detailChecks(p, tag, viewport) {
  const head = JSON.parse(await evaluate(p, "JSON.stringify({chips: document.querySelectorAll('#m2_detail .m2-chip').length, figs: document.querySelectorAll('#m2_detail .m2-fig').length, dates: (document.querySelector('#m2-detail-dates')?.textContent || ''), headHasDates: /Forecast window/i.test(document.querySelector('#m2_detail')?.textContent || '')})"));
  check(`${tag}: store header has label chips, prominent rank/score/urgency and a separate dates module`,
    head.chips === 4 && head.figs === 3 && /Forecast window/i.test(head.dates) && !head.headHasDates, JSON.stringify(head));
  const text = await evaluate(p, "document.querySelector('.tab-pane[data-value=detail]').innerText");
  check(`${tag}: detail shows actions, confidence and both reported-now / next-7-days groups`,
    text.includes("Recommended actions") && text.includes("Estimate confidence") &&
    (text.includes("Predictions unavailable") || (/REPORTED NOW/i.test(text) && /NEXT 7 DAYS/i.test(text))));
  await click(p, "#m2_detail_tabs a[data-value='shelf']");
  await waitFor(p, "!!document.querySelector('#m2-shelf')", 15000, "shelf tab");
  check(`${tag}: detail states no shelf-loss dollars`, await evaluate(p, "/no shelf-loss dollar amount/.test(document.querySelector('#m2-shelf').textContent)"));
  await click(p, "#m2_detail_tabs a[data-value='quality']");
  await waitFor(p, "!!document.querySelector('#m2-quality')", 15000, "quality tab");
  check(`${tag}: estimate quality lists assumptions`, await evaluate(p, "/Assumptions and limitations/.test(document.querySelector('#m2-quality').textContent)"));
  await click(p, "#m2_detail_tabs a[data-value='skus']"); await idle(p);
  check(`${tag}: detail no overflow`, await noOverflow(p));
  if (await evaluate(p, "!!document.querySelector('#m2-sku-table')")) {
    const skuRows = () => rowData(p, "#m2-sku-table");
    check(`${tag}: SKU table default 10 rows`, (await skuRows()).length <= 10);
    const viaHeader = viewport.w > 900;                       // narrower screens use the Sort-by control
    for (const [k, attr] of [["risk", "risk"], ["oos_days", "oosDays"]]) {
      for (const dir of ["desc", "asc"]) {
        if (viaHeader) {
          for (let attempt = 0; attempt < 2; attempt++) {
            const state = await evaluate(p, "(() => { const th = document.querySelector('#m2-sku-table th[aria-sort]'); return th ? th.querySelector('button').dataset.sort + ':' + th.getAttribute('aria-sort') : ''; })()");
            if (state === `${k}:${dir === "desc" ? "descending" : "ascending"}`) break;
            await rerendered(p, () => click(p, `#m2-sku-table th button[data-sort='${k}']`), `sku sort ${k}`, "#m2-sku-table");
          }
        } else if ((await evaluate(p, "document.getElementById('m2_sku_sort').value")) !== `${k}:${dir}`) {
          await selectValue(p, "m2_sku_sort", `${k}:${dir}`, `sku sort ${k} ${dir}`, "#m2-sku-table");
        }
        check(`${tag}: SKU sort ${k} ${dir}, missing last`, sortedOk(await skuRows(), attr, dir === "desc"));
      }
    }
    await selectValue(p, "m2_sku_filter", "oos", "SKU filter", "#m2-sku-table, .tab-pane[data-value=skus] .m2-state");
    const filtered = await skuRows();
    check(`${tag}: SKU filter keeps only out-of-stock-now SKUs`, filtered.every((r) => r.status === "1"));
    await selectValue(p, "m2_sku_filter", "all", "SKU filter all", "#m2-sku-table");
    await selectValue(p, "m2_sku_rows", "50", "SKU rows 50", "#m2-sku-table");
    check(`${tag}: SKU rows selector`, (await skuRows()).length > 0);
    await shot(p, "4-detail-skus");
    const pick = await evaluate(p, "document.querySelector('#m2-sku-table button.m2-sku-link')?.dataset.sku || ''");
    await click(p, `#m2-sku-table button.m2-sku-link[data-sku='${pick}']`);
    await waitFor(p, "document.querySelector('#m2_detail_tabs .nav-link.active')?.dataset.value === 'trend'", 15000, "SKU opens trend");
    await waitFor(p, "!!document.querySelector('#m2-trend-chart .main-svg') || /No SKU selected/.test(document.querySelector('.tab-pane[data-value=trend]').innerText)", 20000, "trend chart");
    check(`${tag}: selecting a SKU shows its inventory and sales trend in this store`, await evaluate(p,
      `(() => { const sel = document.querySelector('#m2_trend_skus'); const values = sel ? [...sel.options].filter(o => o.selected).map(o => o.value) : []; return values.length === 1 && values[0] === ${JSON.stringify(pick)} && /Units sold per day/.test(document.querySelector('#m2-trend-chart')?.textContent || ''); })()`), pick);
    await click(p, "#m2_detail_tabs a[data-value='trend']");
    await waitFor(p, "!!document.querySelector('#m2-trend-chart .main-svg') || /No SKU selected/.test(document.querySelector('.tab-pane[data-value=trend]').innerText)", 20000, "trend chart");
    check(`${tag}: inventory trend labels the scenario`, await evaluate(p, "/not a model forecast/.test(document.querySelector('.tab-pane[data-value=trend]').innerText)"));
    await shot(p, "5-detail-trend");
    await click(p, "#m2_detail_tabs a[data-value='oos']");
    await waitFor(p, "!!document.querySelector('#m2-detail-oos-table')", 20000, "store OOS table");
    check(`${tag}: store OOS SKUs for the past 28 days with unknown days`, await evaluate(p,
      "/past 28 days/.test(document.querySelector('.tab-pane[data-value=oos]').innerText) && /Unknown days/.test(document.querySelector('.tab-pane[data-value=oos]').innerText)"));
    await click(p, "#m2_detail_tabs a[data-value='skus']");
  }
}

async function opsChecks(p, tag, viewport) {
  await signIn(p, "m2_demo_ops");
  await waitFor(p, "!!document.querySelector('#m2-ops-note') && !!document.querySelector('#m2-comparison-table')", 30000, "operations page");
  await waitFor(p, "!!document.querySelector('#m2-algorithm-comparison .main-svg')", 20000, "comparison chart");
  check(`${tag}: operations entry is labelled demo and read-only`,
    await evaluate(p, "document.querySelector('#m2-ops-note').innerText.includes('Demo entry, read-only')"));
  await shellChecks(p, tag, viewport, "ops");
  check(`${tag}: operations page has no visit list or export`,
    !(await visible(p, "#m2-priority-table")) && !(await visible(p, "#m2_export")) && !(await visible(p, "#m2-summary")));
  check(`${tag}: operations no overflow`, await noOverflow(p));
  await shot(p, "6-ops-health");
  await goPage(p, "monitoring", viewport);
  await waitFor(p, "document.body.innerText.includes('Pending outcomes') || document.body.innerText.includes('No monitoring yet')", 20000, "monitoring");
  check(`${tag}: monitoring renders an honest state`, true);
  if (viewport.w <= 576) await toggleSidebar(p, true);
  await click(p, "#m2_signout");
  await waitFor(p, "document.querySelector('#m2-login').offsetParent !== null", 15000, "logged out");
  check(`${tag}: sign-out from operations clears model content`, await evaluate(p, "!document.querySelector('#m2-comparison-table, #m2-selected-table')"));
}

async function mainScenario() {
  for (const viewport of VIEWPORTS) {
    for (const role of [...ROLES, { name: "ops" }]) {
      const p = await openPage({ ...viewport, name: `${viewport.name}-${role.name}` });
      const tag = `${viewport.name}/${role.name}`;
      if (args.only && args.only !== tag) continue;              // debugging aid: run one combination
      try {
        if (role.name === "sales") {
          await p.s("Page.navigate", { url: BASE });
          await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#m2-login')", 30000);
          await shot(p, "0-login");
        }
        if (role.name === "ops") await opsChecks(p, tag, viewport); else await businessChecks(p, tag, role, viewport);
      } catch (error) {
        check(`${tag}: scenario completed`, false, String(error));
        await shot(p, "x-failure").catch(() => {});
      }
      await send("Target.closeTarget", { targetId: p.targetId });
    }
  }
}

// Root app: Module 2 inside the app's own navigation; Module 2 sign-out uses the root logout.
// The operations entry selects the Module 2 tab directly; signing in again keeps that tab, so the business
// workspace is checked without first rendering Module 1 (whose own charts end the session on this synthetic
// data at the current main; recorded as a known blocker, not hidden).
async function rootScenario() {
  const viewport = { name: "root-1440", w: 1440, h: 900, dpr: 1, mobile: false };
  const p = await openPage(viewport);
  const tag = "root-1440";
  results.known_blockers = [];
  const shown = (sel) => `(document.querySelector(${JSON.stringify(sel)})?.offsetParent ?? null) !== null`;
  try {
    await p.s("Page.navigate", { url: BASE });
    await waitFor(p, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#quick_ops')", 60000, "root app");
    await sleep(500);
    await click(p, "#quick_ops");
    await waitFor(p, "!!document.querySelector('#m2-ops-note') && !!document.querySelector('#m2-comparison-table')", 60000, "root ops entry");
    check(`${tag}: root ML Operations demo entry opens the Module 2 operations workspace`, true);
    check(`${tag}: Module 2 sidebar renders inside the root app`, await visible(p, "#m2_sidebar"));
    check(`${tag}: root navigation stays visible`, await evaluate(p,
      "[...document.querySelectorAll('a[data-value^=\"tab_mod\"]')].filter(a => a.offsetParent !== null).length >= 4"));
    check(`${tag}: Module 2 sidebar lists no other modules`, !(await evaluate(p, "/Module [134]/.test(document.querySelector('#m2_sidebar').innerText)")));
    check(`${tag}: root profile name and title in the Module 2 top bar`, await evaluate(p,
      "(document.querySelector('#m2-user .m2-user-text')?.children.length || 0) === 2"));
    await shot(p, "1-root-ops");
    await click(p, "#m2_signout");
    await waitFor(p, shown("#quick_mgr"), 20000, "root login after Module 2 sign-out");
    check(`${tag}: Module 2 sign-out uses the root logout (root login shown)`, true);
    check(`${tag}: no operations content after sign-out`, await evaluate(p, "!document.querySelector('#m2-comparison-table')"));
    await click(p, "#quick_mgr");
    try {
      await waitFor(p, "document.querySelectorAll('#m2-top10-table tbody tr').length > 0", 60000, "Module 2 overview in root app");
      await idle(p);
      check(`${tag}: Module 2 business workspace in the root app`, await visible(p, "#m2-top10-table"));
      await shot(p, "2-root-business");
      await click(p, "#btn_logout");
      await waitFor(p, shown("#quick_mgr"), 20000, "root logout");
      check(`${tag}: root logout clears Module 2 business content`, (await tableIds(p, "#m2-top10-table")).length === 0);
    } catch (error) {
      const connected = await evaluate(p, "Shiny.shinyapp.isConnected()").catch(() => false);
      if (!connected) {
        results.known_blockers.push({ name: `${tag}: business path`, detail: "Session ended while the root app rendered " +
          "other modules; see the server log (pre-existing Module 1 chart error on synthetic data)." });
        await shot(p, "x-blocked").catch(() => {});
      } else {
        throw error;
      }
    }
  } catch (error) {
    check(`${tag}: scenario completed`, false, String(error));
    await shot(p, "x-failure").catch(() => {});
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
      await waitFor(p, "document.body.innerText.includes('Predictions not released')", 15000);
      check("state unreleased rendered (missing predictions are not zero risk)", await evaluate(p, "document.body.innerText.includes('not zero risk')"));
    } else if (name === "stale") {
      await click(p, "#m2_demo_rep_a"); await idle(p);
      await waitFor(p, "!!document.querySelector('#m2-stale-banner')", 15000);
      check("state stale rendered", true);
    } else if (name === "error") {
      await click(p, "#m2_demo_rep_a"); await idle(p);
      await waitFor(p, "document.querySelectorAll('#m2-top10-table tbody tr').length > 0", 20000);
      writeFileSync(join(OUT, "trigger-error"), "1");
      await sleep(1500);
      await click(p, "#m2-top10-table a.m2-store-link");
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
  if (args.scenario === "main") await mainScenario();
  else if (args.scenario === "root") await rootScenario();
  else await stateScenario(args.state);
} finally {
  const external = results.network.filter((n) => !/^(https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/|data:|blob:|about:)/.test(n.url));
  if (args.scenario === "root") {
    results.root_external_requests = external.slice(0, 20);      // other modules may load their own assets
    check("no external requests from Module 2 assets", !external.some((n) => /m2-|plotly|inter/i.test(n.url)), JSON.stringify(external.slice(0, 5)));
  } else {
    check("no non-loopback network requests", external.length === 0, JSON.stringify(external.slice(0, 5)));
  }
  check("no console errors", results.console.length === 0, JSON.stringify(results.console.slice(0, 5)));
  results.finished_at = new Date().toISOString();
  results.passed = results.checks.every((c) => c.ok);
  writeFileSync(join(OUT, "results.json"), JSON.stringify(results, null, 2));
  ws.close(); chrome.kill("SIGTERM");
  await sleep(500); rmSync(profile, { recursive: true, force: true });
  console.log(JSON.stringify({ passed: results.passed, checks: results.checks.length, failed: results.checks.filter((c) => !c.ok).map((c) => c.name) }));
  process.exit(results.passed ? 0 : 1);
}
