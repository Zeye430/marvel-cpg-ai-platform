// Integrated Module 2 + Module 4 browser acceptance through Chrome DevTools Protocol (no npm deps).
// Usage: node integrated_acceptance.mjs --scenario main|unavailable|nomodel|candidate|unreleased|pg|pgrevoked --url URL
//        --out DIR --manager-stores N --rep-stores N --as-of DATE --artifact ID
// Launches local headless Chrome with a temporary profile and loopback-only debugging, drives the
// real Shiny app and writes screenshots, results.json and console/network logs.
// Synthetic data and a deterministic model stub only.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? acc.concat([[v.slice(2), a[i + 1]]]) : acc), []));
const CHROME = args.chrome || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUT = args.out; mkdirSync(OUT, { recursive: true });
const BASE = args.url;
const SCENARIO = args.scenario;
const EXPECT = { manager: Number(args["manager-stores"] || 0), rep: Number(args["rep-stores"] || 0),
  asOf: args["as-of"], artifact: args.artifact };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = { scenario: SCENARIO, url: BASE, checks: [], screenshots: [], network: [], console: [], started_at: new Date().toISOString() };
const check = (name, ok, detail = "") => { results.checks.push({ name, ok: !!ok, detail: String(detail).slice(0, 500) }); if (!ok) console.error("FAIL", name, detail); };

// ---------------------------------------------------------------- Chrome + CDP
const profile = mkdtempSync(join(tmpdir(), "integrated-chrome-"));
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
results.browser = (await send("Browser.getVersion")).product;

async function openPage(viewport) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const s = (method, params) => send(method, params, sessionId);
  await s("Page.enable"); await s("Runtime.enable"); await s("Network.enable"); await s("Log.enable");
  listeners.push((m) => {
    if (m.sessionId !== sessionId) return;
    if (m.method === "Network.requestWillBeSent") results.network.push({ viewport: viewport.name, url: m.params.request.url });
    if (m.method === "Network.loadingFailed") results.network.push({ viewport: viewport.name, failed: m.params.errorText, type: m.params.type });
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") results.console.push({ viewport: viewport.name, text: m.params.args.map((a) => a.value ?? a.description).join(" ") });
    if (m.method === "Runtime.exceptionThrown") results.console.push({ viewport: viewport.name, text: m.params.exceptionDetails.text + " " + (m.params.exceptionDetails.exception?.description || "") });
  });
  await s("Emulation.setDeviceMetricsOverride", { width: viewport.w, height: viewport.h, deviceScaleFactor: 1, mobile: !!viewport.mobile });
  if (viewport.mobile) await s("Emulation.setTouchEmulationEnabled", { enabled: true });
  await s("Page.navigate", { url: BASE });
  const page = { s, targetId, viewport };
  await waitFor(page, "window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected() && !!document.querySelector('#shell_signin_manager')", 60000, "app connected");
  await sleep(300);
  return page;
}
async function evaluate(p, expression) {
  const r = await p.s("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " :: " + expression.slice(0, 80));
  return r.result.value;
}
async function waitFor(p, expression, timeout = 30000, label = expression) {
  const start = Date.now();
  while (Date.now() - start < timeout) { try { if (await evaluate(p, expression)) return true; } catch (e) { /* loading */ } await sleep(200); }
  throw new Error("Timed out waiting for " + label);
}
async function soft(p, expression, timeout, label) {
  try { await waitFor(p, expression, timeout, label); return true; } catch (e) { return false; }
}
const idle = (p) => waitFor(p, "!document.documentElement.classList.contains('shiny-busy') && ![...document.querySelectorAll('.recalculating')].some(e => e.offsetParent !== null)", 60000, "shiny idle");
async function click(p, selector) {
  const rect = await evaluate(p, `new Promise((resolve) => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return resolve(null); e.scrollIntoView({block: 'center', behavior: 'instant'}); requestAnimationFrame(() => requestAnimationFrame(() => { const r = e.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); resolve({x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, ok: !!hit && (hit === e || e.contains(hit) || hit.contains(e)), hit: hit ? (hit.tagName + '#' + hit.id + '.' + hit.className) : ''}); })); })`);
  if (!rect || !rect.w) throw new Error("Not clickable: " + selector);
  if (!rect.ok) throw new Error("Click target obscured: " + selector + " by " + rect.hit);
  if (p.viewport.mobile) {
    await p.s("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: rect.x, y: rect.y }] });
    await p.s("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    return;
  }
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await p.s("Input.dispatchMouseEvent", { type, x: rect.x, y: rect.y, button: "left", clickCount: 1 });
}
async function shot(p, name) {
  const metrics = await p.s("Page.getLayoutMetrics");
  const { data } = await p.s("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: p.viewport.w, height: Math.min(Math.ceil(metrics.cssContentSize.height), 6000), scale: 1 } });
  const file = `${SCENARIO}-${p.viewport.name}-${name}.png`;
  writeFileSync(join(OUT, file), Buffer.from(data, "base64"));
  results.screenshots.push({ file, viewport: `${p.viewport.w}x${p.viewport.h}` });
}
const txt = (p, selector) => evaluate(p, `(document.querySelector(${JSON.stringify(selector)}) || {textContent: ''}).textContent`);
const visible = (p, selector) => evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(selector)}); return !!e && e.offsetParent !== null; })()`);
const overflow = (p) => evaluate(p, "document.documentElement.scrollWidth - window.innerWidth");
const pageText = (p) => evaluate(p, "document.body.innerText");

// ---------------------------------------------------------------- app steps
async function signIn(p, key, principal) {
  if (!(await visible(p, "#shell-login"))) await signOut(p);
  await click(p, `#shell_signin_${key}`);
  await waitFor(p, `document.querySelector('#shell-identity')?.dataset.principal === '${principal}'`, 20000, "signed in " + principal);
  await waitFor(p, "document.querySelector('#shell-panel-module_1')?.offsetParent !== null", 20000, "module 1 shown");
  await idle(p);
}
// The app sidebar is an overlay on phones: open it before using its controls.
async function sidebar(p) {
  await waitFor(p, "!document.querySelector('#shell-app .bslib-sidebar-layout')?.classList.contains('transitioning')", 10000, "sidebar settled");
  if (await visible(p, "#shell-nav")) return;
  await click(p, "#shell-app .bslib-sidebar-layout > .collapse-toggle");
  await waitFor(p, "document.querySelector('#shell-nav')?.offsetParent !== null && !document.querySelector('#shell-app .bslib-sidebar-layout').classList.contains('transitioning')", 10000, "sidebar open");
  await sleep(400);
}
async function signOut(p) {
  await evaluate(p, "window.scrollTo(0, 0) || true");
  await sidebar(p);
  await click(p, "#shell_signout");
  await waitFor(p, "document.querySelector('#shell-login')?.offsetParent !== null", 20000, "signed out");
  await sleep(300);
}
async function openModule(p, key) {
  await evaluate(p, "window.scrollTo(0, 0) || true");
  await sidebar(p);
  await click(p, `#shell-nav button[data-module='${key}']`);
  await waitFor(p, `document.querySelector('#shell-panel-${key}')?.offsetParent !== null && document.querySelector("#shell-nav button[data-module='${key}']")?.getAttribute('aria-current') === 'page'`, 20000, "open " + key);
  await idle(p);
}
async function moduleTwoReady(p) {
  if (!(await visible(p, "#shell-panel-module_2"))) await openModule(p, "module_2");
  await waitFor(p, "!!document.querySelector('#m2_header') || !!document.querySelector('#m2-root .m2-banner.bad') || !!document.querySelector('#m2-root .m2-state')", 60000, "module 2 content");
  await idle(p);
}
// Module 1 (Business Performance): KPI tiles, the four chart pages, rep locks; no widget errors.
const M1_PAGES = { geo: [2, 0], oos: [3, 0], shelf: [3, 0], trade: [2, 0] };
const M1_REP = { geo: [0, 2], oos: [1, 2], shelf: [1, 2], trade: [1, 1] };
// Records every welcome greeting shown from now on (the load can be quick on the synthetic data).
async function watchWelcome(p) {
  await evaluate(p, `(() => { window.__m1Welcome = []; if (window.__m1WelcomeObs) window.__m1WelcomeObs.disconnect();
    const seen = () => { const w = document.querySelector('#m1-welcome'); if (w) { const t = w.innerText.replace(/\\s+/g, ' ').trim(); if (!window.__m1Welcome.includes(t)) window.__m1Welcome.push(t); } };
    window.__m1WelcomeObs = new MutationObserver(seen); window.__m1WelcomeObs.observe(document.body, {childList: true, subtree: true, characterData: true}); return true; })()`);
}
// Filter pills: current values on the pills, a changed filter is highlighted with Reset, Reset restores defaults.
async function filterPillChecks(p) {
  const pill = (key) => txt(p, `#m1-pill-${key} > summary`);
  await waitFor(p, "/Last 13 weeks/.test(document.querySelector('#m1-pill-dates > summary')?.innerText || '')", 20000, "date pill");
  check("filters: one row of pills with the current values", /Last 13 weeks/.test(await pill("dates")) && /Channel\s*All/.test(await pill("channel")) && /Region\s*All/.test(await pill("region")) && /SKU\s*All \d+/.test(await pill("sku")) && !(await evaluate(p, "!!document.querySelector('#m1-reset')")), [await pill("dates"), await pill("channel"), await pill("sku")].join(" | "));
  check("filters: options stay closed until a pill is opened", !(await evaluate(p, "[...document.querySelectorAll('#m1-root details.m1-pill')].some(d => d.open)")));
  await click(p, "#m1-pill-sku > summary");
  await waitFor(p, "document.querySelector('#m1-pill-sku')?.open && document.querySelector('#m1-pill-sku .m1-pop').checkVisibility()", 10000, "SKU options");
  check("filters: SKU options list products under their category headings", (await evaluate(p, "document.querySelectorAll('#m1_sku .m1-sku-group').length")) >= 1);
  await click(p, "#m1_sku_none");
  await waitFor(p, "/SKU\\s*None/.test(document.querySelector('#m1-pill-sku > summary')?.innerText || '') && !!document.querySelector('#m1-reset')", 20000, "SKU cleared");
  await idle(p);
  check("filters: a changed pill is highlighted and Reset appears", await evaluate(p, "!!document.querySelector('#m1-pill-sku .m1-pill-val.set')") && /No SKU selected/.test(await txt(p, "#m1-kpis-wrap, #m1_kpis")));
  await click(p, "#m1-reset");
  await waitFor(p, "/SKU\\s*All \\d+/.test(document.querySelector('#m1-pill-sku > summary')?.innerText || '') && !document.querySelector('#m1-reset')", 20000, "filters reset");
  await idle(p);
  check("filters: Reset restores every product and the KPIs", (await evaluate(p, "document.querySelectorAll('#m1-root .m1-kpi').length")) === 5);
  await click(p, "#m1-pill-dates > summary");
  await waitFor(p, "document.querySelector('#m1-pill-dates')?.open && document.querySelector('#m1-pill-dates .m1-pop').checkVisibility()", 10000, "date options");
  await click(p, "#m1-pill-dates .m1-preset:nth-child(1)");
  await waitFor(p, "/Last 4 weeks/.test(document.querySelector('#m1-pill-dates > summary')?.innerText || '')", 20000, "4-week preset");
  await idle(p);
  check("filters: a date preset sets the range and highlights the pill", await evaluate(p, "!!document.querySelector('#m1-pill-dates .m1-pill-val.set') && !!document.querySelector('#m1-reset')"));
  await click(p, "#m1-reset");
  await waitFor(p, "/Last 13 weeks/.test(document.querySelector('#m1-pill-dates > summary')?.innerText || '') && !document.querySelector('#m1-reset')", 20000, "dates reset");
  await idle(p);
  // Every preset answers, including one that covers the same weeks as the current range.
  await click(p, "#m1-pill-dates > summary");
  await waitFor(p, "document.querySelector('#m1-pill-dates')?.open", 10000, "date options again");
  await click(p, "#m1-pill-dates .m1-preset:nth-child(3)");
  await waitFor(p, "/Quarter to date/.test(document.querySelector('#m1-pill-dates > summary')?.innerText || '')", 20000, "quarter-to-date preset");
  await idle(p);
  check("filters: Quarter to date is shown as chosen (even where it covers the last 13 weeks)", await evaluate(p, "document.querySelector('#m1-pill-dates .m1-preset.on')?.innerText === 'Quarter to date' && !!document.querySelector('#m1-reset')"));
  await click(p, "#m1-reset");
  await waitFor(p, "/Last 13 weeks/.test(document.querySelector('#m1-pill-dates > summary')?.innerText || '') && !document.querySelector('#m1-reset')", 20000, "dates reset again");
  await idle(p);
}
// KPI cards: compact; the selected card's trend (with target), notes and collapsed calculation open below.
async function kpiDetailChecks(p) {
  const detailReady = (title) => `/${title}/.test(document.querySelector('#m1-kpi-detail h2')?.innerText || '') && (!!document.querySelector('#m1-kpi-detail .js-plotly-plot') || /No trend/.test(document.querySelector('#m1-kpi-detail')?.innerText || ''))`;
  await waitFor(p, detailReady("MSL Compliance"), 30000, "KPI detail");
  await idle(p);
  check("KPI cards: the first card is selected and its trend shows below with the team target line", (await evaluate(p, "document.querySelector('#m1-kpi-msl').getAttribute('aria-pressed') === 'true'")) && (await evaluate(p, "(() => { const g = document.querySelector('#m1-kpi-detail .js-plotly-plot'); return !g || (g.layout.shapes || []).some(s => s.type === 'line'); })()")) && /Team target ≥ 90%|No trend/.test(await txt(p, "#m1-kpi-detail")));
  check("KPI cards: no small print or calculation inside the cards", !/How it's calculated|coverage|not market share|known SKU-days/i.test(await txt(p, "#m1-kpis")), await txt(p, "#m1-kpis"));
  check("KPI detail: notes below the chart, calculation collapsed", /team display target/.test(await txt(p, "#m1-kpi-detail .m1-notes")) && !(await evaluate(p, "document.querySelector('#m1-kpi-how').open")));
  await click(p, "#m1-kpi-how > summary");
  check("KPI detail: the calculation opens on request", await evaluate(p, "document.querySelector('#m1-kpi-how').open") && /Formula/.test(await txt(p, "#m1-kpi-how")));
  await click(p, "#m1-kpi-oos");
  await waitFor(p, detailReady("Reported OOS Rate"), 30000, "OOS detail");
  await idle(p);
  check("KPI cards: selecting another card shows its trend instead", (await evaluate(p, "document.querySelector('#m1-kpi-oos').getAttribute('aria-pressed') === 'true' && document.querySelector('#m1-kpi-msl').getAttribute('aria-pressed') === 'false'")) && /Team target ≤ 5%|No trend/.test(await txt(p, "#m1-kpi-detail")));
  await click(p, "#m1-kpi-msl");
  await waitFor(p, detailReady("MSL Compliance"), 30000, "MSL detail again");
  await idle(p);
  // MSL products pill narrows the products the page counts.
  const skus = async () => Number(((await txt(p, "#m1_filter_summary")).match(/(\d+) SKUs/) || [])[1]);
  const before = await skus();
  await click(p, "#m1-pill-msl > summary");
  await waitFor(p, "document.querySelector('#m1-pill-msl')?.open", 10000, "MSL options");
  await click(p, "#m1_msl input[value='msl']");
  await waitFor(p, "/MSL only/.test(document.querySelector('#m1-pill-msl > summary')?.innerText || '') && !!document.querySelector('#m1-reset')", 20000, "MSL only");
  await idle(p);
  const after = await skus();
  check("filters: MSL only is highlighted and counts only must-stock products", (await evaluate(p, "!!document.querySelector('#m1-pill-msl .m1-pill-val.set')")) && after > 0 && after <= before, `${before} -> ${after}`);
  await click(p, "#m1-reset");
  await waitFor(p, "/Products\\s*All/.test(document.querySelector('#m1-pill-msl > summary')?.innerText || '') && !document.querySelector('#m1-reset')", 20000, "MSL reset");
  await idle(p);
}
// Module 3 (Negotiation Simulator): the deal simulator's three verdicts and the price-change response.
async function moduleThreeChecks(p, tag, stores, full) {
  await openModule(p, "module_3");
  await waitFor(p, "!!document.querySelector('#m3-context') || !!document.querySelector('#m3-unavailable')", 120000, "module 3 content");
  await idle(p);
  const head = await txt(p, "#shell-panel-module_3 .ws-pagehead");
  check(`${tag}: Negotiation Simulator reads the shared source in the signed-in scope`, (stores === null || head.includes(`${stores} stores in your demo scope`)) && head.includes(`Data through ${EXPECT.asOf}`) && /Rules pending client confirmation/.test(head), head);
  check(`${tag}: Module 3 pages listed under Negotiation Simulator`, (await evaluate(p, "[...document.querySelectorAll(\"#shell-nav .ws-subnav[data-module='module_3'] button[data-page]\")].map(b => b.dataset.page).join(',')")) === "deal,price");
  const accounts = await evaluate(p, "Object.keys(document.querySelector('#m3_store').selectize.options).length");
  check(`${tag}: only accounts in the signed-in scope can be chosen`, accounts > 0 && (stores === null || accounts <= stores), `${accounts}`);
  const verdict = (k) => `document.querySelector('#m3_verdict_${k} [data-verdict]')?.dataset.verdict`;
  await waitFor(p, `!!${verdict("a")} && !!${verdict("b")} && !!${verdict("c")}`, 30000, "verdicts");
  await idle(p);
  const verdicts = await evaluate(p, `[${verdict("a")}, ${verdict("b")}, ${verdict("c")}]`);
  check(`${tag}: three proposals side by side, each with a guardrail verdict`, verdicts.every((v) => ["APPROVED", "APPROVED WITH CONDITIONS", "REJECTED", "CANNOT ASSESS"].includes(v)) && (await evaluate(p, "document.querySelectorAll('#m3-scenarios .m3-scn').length")) === 3, JSON.stringify(verdicts));
  check(`${tag}: the context shows the guardrail as Requires review`, /Requires review/.test(await txt(p, "#m3-context")) && /Maximum discount without extra approval/.test(await txt(p, "#m3-context")));
  check(`${tag}: no Module 3 error and the session stays connected`, (await evaluate(p, "document.querySelectorAll('#m3-root .shiny-output-error, #m3-root .ws-state.bad').length")) === 0 && (await evaluate(p, "Shiny.shinyapp.isConnected()")));
  if (!full) return;
  check("deal: the starting proposals show the trade-off (with the commitment it is conditional)", verdicts[2] === "APPROVED WITH CONDITIONS" || verdicts[2] === "CANNOT ASSESS", JSON.stringify(verdicts));
  check("deal: margin chart with the minimum-margin line", await evaluate(p, "(() => { const g = document.querySelector('#m3-margin-chart'); return !!g && (g.layout.shapes || []).some(s => s.type === 'line'); })()"));
  if (verdicts[2] === "APPROVED WITH CONDITIONS") {
    await evaluate(p, "(() => { const s = document.querySelector('#m3_commit_c'); s.value = 'none'; window.jQuery(s).trigger('change'); return true; })()");
    await waitFor(p, `${verdict("c")} === 'REJECTED'`, 20000, "commitment withdrawn");
    check("deal: withdrawing the commitment turns the conditional proposal into REJECTED", true);
  }
  await click(p, "#m3-assumptions > summary");
  await soft(p, "/m3-rules/.test(document.querySelector('#m3-rules')?.innerText || '')", 10000, "rules text");
  check("deal: rules and assumptions are stated and switchable", /m3-rules\.v0-proposed/.test(await txt(p, "#m3-rules")) && (await evaluate(p, "document.querySelectorAll('#m3_basis input, #m3_uplift input').length")) === 4);
  await shot(p, "9-module3-deal");
  await click(p, "#shell-nav .ws-subnav[data-module='module_3'] button[data-page='price']");
  await waitFor(p, "!!document.querySelector('#m3-b-cards') || /No observed sales|No elasticity/.test(document.querySelector('#m3_b_results')?.innerText || '')", 60000, "price page");
  await idle(p);
  await soft(p, "!!document.querySelector('#m3-price-chart')", 30000, "price chart");
  await idle(p);
  check("price: volume, revenue, gross profit and elasticity cards with the response curve", (await evaluate(p, "document.querySelectorAll('#m3-b-cards .m3-b-card').length")) === 4 && await evaluate(p, "!!document.querySelector('#m3-price-chart')"), await txt(p, "#m3_b_results"));
  check("price: the assumptions and limits are stated", /not complete demand/.test(await txt(p, "#m3-b-notes")) && /not a forecast/.test(await txt(p, "#m3-b-notes")));
  await evaluate(p, "(() => { const r = document.querySelector(\"#m3_b_source input[value='custom']\"); r.checked = true; window.jQuery(r).trigger('change'); return true; })()");
  await waitFor(p, "!!document.querySelector('#m3_b_custom')", 10000, "custom elasticity");
  await evaluate(p, "(() => { const n = document.querySelector('#m3_b_custom'); n.value = '-2'; window.jQuery(n).trigger('change'); return true; })()");
  await waitFor(p, "/-2\\.00/.test(document.querySelector('#m3-b-cards')?.innerText || '')", 20000, "custom elasticity applied");
  check("price: a custom elasticity is used and labelled as an assumption", /Custom assumption/.test(await txt(p, "#m3-b-cards")));
  await shot(p, "10-module3-price");
  await evaluate(p, "m3Go('deal') || true");
  await idle(p);
}
async function moduleOneReady(p) {
  await waitFor(p, "!!document.querySelector('#m1-kpis') || !!document.querySelector('#m1-unavailable')", 120000, "module 1 content");
  await idle(p);
}
async function moduleOneChecks(p, tag, stores, manager) {
  await openModule(p, "module_1");
  await moduleOneReady(p);
  const head = await txt(p, "#shell-panel-module_1 .ws-pagehead");
  check(`${tag}: Business Performance reads the shared source in the signed-in scope`, (stores === null || head.includes(`${stores} stores in your demo scope`)) && head.includes(`Data through ${EXPECT.asOf}`) && /Reported data only/.test(head) && head.includes(manager ? "Regional manager view" : "Field sales rep view"), head);
  check(`${tag}: five KPI tiles`, (await evaluate(p, "document.querySelectorAll('#m1-kpis .m1-kpi').length")) === 5);
  check(`${tag}: Module 1 pages listed under Business Performance`, (await evaluate(p, "[...document.querySelectorAll(\"#shell-nav .ws-subnav[data-module='module_1'] button[data-page]\")].map(b => b.dataset.page).join(',')")) === "overview,geo,oos,shelf,trade");
  const counts = {};
  for (const [page, [plots, locked]] of Object.entries(manager ? M1_PAGES : M1_REP)) {
    await evaluate(p, `m1Go('${page}') || true`);
    const pane = `document.querySelector("#m1-root .tab-pane[data-value='${page}']")`;
    await soft(p, `${pane}?.classList.contains('active') && ${pane}.querySelectorAll('.js-plotly-plot, .m1-locked, .m1-state').length >= ${plots + locked}`, 60000, "module 1 " + page);
    await idle(p);
    counts[page] = await evaluate(p, `[${pane}.querySelectorAll('.js-plotly-plot').length, ${pane}.querySelectorAll('.m1-locked').length]`);
  }
  check(`${tag}: every Module 1 chart renders (manager: 10) or is locked for the rep (3 shown, 7 locked)`, JSON.stringify(counts) === JSON.stringify(manager ? M1_PAGES : M1_REP), JSON.stringify(counts));
  check(`${tag}: no Module 1 widget error and the session stays connected`, (await evaluate(p, "document.querySelectorAll('#m1-root .shiny-output-error, #m1-root .m1-state.bad').length")) === 0 && (await evaluate(p, "Shiny.shinyapp.isConnected()")));
  await evaluate(p, "m1Go('overview') || true");
  await idle(p);
}
async function ask(p, question) {
  const before = await evaluate(p, "document.querySelectorAll('#m4-root .m4-turn').length");
  await evaluate(p, `(() => { const t = document.querySelector('#m4_user_query'); t.value = ${JSON.stringify(question)}; t.dispatchEvent(new Event('input', {bubbles: true})); t.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`);
  await sleep(250);
  await click(p, "#m4_btn_ask");
  return before;
}
async function answered(p, question, timeout = 60000) {
  await waitFor(p, `[...document.querySelectorAll('#m4-root .m4-turn')].some(t => t.querySelector('.m4-q')?.textContent === ${JSON.stringify(question)} && !t.querySelector('.m4-thinking'))`, timeout, "answer to " + question.slice(0, 30));
  return evaluate(p, `(() => { const t = [...document.querySelectorAll('#m4-root .m4-turn')].find(t => t.querySelector('.m4-q')?.textContent === ${JSON.stringify(question)}); return {text: t.innerText, provenance: t.querySelector('.m4-provenance')?.textContent || '', warn: !!t.querySelector('.m4-warn'), table: !!t.querySelector('.m4-table'), cites: !!t.querySelector('.m4-cites'), passages: t.querySelectorAll('.m4-passage').length, note: t.querySelector('.m4-model-note')?.textContent || '', columns: [...t.querySelectorAll('.m4-table th')].map(h => h.textContent.trim()), firstColumn: [...t.querySelectorAll('.m4-table tbody tr')].map(r => r.cells[0].textContent.trim())}; })()`);
}
const DATA_Q = "How many units were sold in each channel?";
const RISK_Q = "Which SKUs in my stores have a stockout alert?";
const STORED_Q = "Show the visit priority ranking of my stores";
const BUSINESS_Q = "What is the business priority ranking in Module 2?";
const RUN = /scoring run score-\d{4}-\d{2}-\d{2}-\w+/;

async function predictionChecks(p, tag, stores, disclosure) {
  await ask(p, STORED_Q);
  const stored = await answered(p, STORED_Q);
  const ranks = stored.firstColumn.map(Number);
  check(`${tag}: stored visit ranking answer with table and run provenance`, stored.table && RUN.test(stored.provenance) && stored.provenance.includes("store_priority"), stored.provenance);
  check(`${tag}: stored rank is renumbered within the scope (1..n, no global rank column)`, ranks.length > 0 && ranks.length <= stores && ranks.every((r, i) => r === i + 1) && stored.columns.includes("stored_rank_in_scope") && !stored.columns.includes("priority_rank"), JSON.stringify({ ranks, columns: stored.columns }));
  check(`${tag}: stored ranking note names scope and v1 ranking`, stored.note.includes(`renumbered within ${stores === EXPECT.manager ? "all " + stores + " stores" : "your " + stores + " assigned stores"}`) && /not a probability that the store runs out of stock/.test(stored.note), stored.note);
  check(`${tag}: model status disclosed`, disclosure.test(stored.note), stored.note);
  check(`${tag}: prediction is not presented as an observed stockout`, /not an observed stockout/.test(stored.note) && /not zero risk/.test(stored.note), stored.note);
  await ask(p, BUSINESS_Q);
  const business = await answered(p, BUSINESS_Q);
  check(`${tag}: business ranking answer is Module 2's proposed policy, distinct from stored`, business.table && business.columns.includes("business_rank") && /business-priority\.v2-proposed/.test(business.note) && /different ordering from the stored v1 rank/.test(business.note), business.note);
  return { stored, business };
}
const DOC_Q = "What argument do I give a client who refuses to take the new degreaser line?";

async function moduleTwoChecks(p, tag, stores) {
  await moduleTwoReady(p);
  const head = await txt(p, "#m2_header");
  check(`${tag}: Module 2 shows the shared as-of date`, head.includes("Reported as of") && (await txt(p, "#shell-source")).includes(EXPECT.asOf), head);
  check(`${tag}: Module 2 scope is the principal's ${stores} stores`, head.includes(`${stores} assigned stores`), head);
  await waitFor(p, "document.querySelectorAll('#m2-top10-table tr[data-open-store]').length > 0", 30000, "top rows");
  check(`${tag}: no raw None ranks in tables`, !/\bNone\b/.test(await evaluate(p, "document.querySelector('#m2-root').innerText")));
}

// ---------------------------------------------------------------- scenarios
async function scenarioMain() {
  const p = await openPage({ name: "desktop", w: 1440, h: 900 });
  const login = await txt(p, "#shell-login");
  check("login: demo-only sign-in is stated", /Demo sign-in only/.test(login) && /not production authentication/.test(login), login);
  check("login: lists the four modules by name, all in this build", /Business Performance\s*Module 1 · Sales, distribution/.test(login) && /Stockout Risk\s*Module 2 · Predicted stockouts/.test(login) && /Negotiation Simulator\s*Module 3 · Test deal terms/.test(login) && /Commercial Copilot\s*Module 4 · Ask questions/.test(login) && !/not in this build/.test(login), login);
  check("login: workspace hidden while signed out", !(await visible(p, "#shell-app")));
  await shot(p, "1-login");

  await watchWelcome(p);
  await signIn(p, "manager", "demo:manager");
  const source = await evaluate(p, "({...document.querySelector('#shell-source').dataset})");
  check("shell: one shared source with as-of and release", source.ready === "yes" && source.asOf === EXPECT.asOf && source.release === EXPECT.artifact, JSON.stringify(source));
  check("shell: Business Performance is the start module", (await evaluate(p, "document.querySelector(\"#shell-nav button[data-module='module_1']\").getAttribute('aria-current')")) === "page");
  await moduleOneReady(p);
  const greeted = await evaluate(p, "window.__m1Welcome || []");
  check("start: a welcome greeting for the signed-in user is shown while Business Performance loads", greeted.some((g) => /(Good morning|Good afternoon|Good evening|Working late), Manager\b/.test(g) && /Getting your business performance ready/.test(g)), JSON.stringify(greeted).slice(0, 300));
  check("start: the welcome makes way for the KPIs", !(await evaluate(p, "!!document.querySelector('#m1-welcome')")) && (await evaluate(p, "document.querySelectorAll('#m1-root .m1-kpi').length")) === 5);
  await openModule(p, "module_2");
  const nav = await evaluate(p, "[...document.querySelectorAll('#shell-nav > button[data-module]')].map(b => ({key: b.dataset.module, text: b.innerText.replace(/\\s+/g, ' ').trim(), disabled: b.getAttribute('aria-disabled') === 'true'}))");
  check("sidebar: four modules by functional name in order, all enabled", JSON.stringify(nav.map((n) => [n.key, n.disabled])) === JSON.stringify([["module_1", false], ["module_2", false], ["module_3", false], ["module_4", false]]) && /^Business Performance Module 1/.test(nav[0].text) && /^Stockout Risk Module 2/.test(nav[1].text) && /^Negotiation Simulator Module 3/.test(nav[2].text) && /^Commercial Copilot Module 4/.test(nav[3].text), JSON.stringify(nav));
  check("sidebar: the only module switcher (no top module bar), one sign-out", (await evaluate(p, "document.querySelectorAll('.shell-nav, #shell-bar').length")) === 0 && (await evaluate(p, "document.querySelectorAll('#m2_signout, #shell_signout').length")) === 1);
  check("sidebar: Module 2 pages listed under Stockout Risk", (await evaluate(p, "[...document.querySelectorAll(\"#shell-nav .ws-subnav[data-module='module_2'] button[data-page]\")].filter(b => b.offsetParent !== null).map(b => b.dataset.page).join(',')")) === "overview,ranking,analytics");
  check("top bar: breadcrumb uses the module name", /Stockout Risk\s*\/\s*Overview/.test(await txt(p, "#m2-root .m2-crumb")), await txt(p, "#m2-root .m2-crumb"));
  await moduleTwoChecks(p, "manager", EXPECT.manager);
  check("manager: synthetic data is disclosed", /Synthetic demo/.test(await txt(p, "#m2-root .m2-banner")));
  await shot(p, "2-module2-overview");

  // Ranking (opened from the sidebar's page link): unranked stores (if any) are explicit, never a number.
  await click(p, "#shell-nav .ws-subnav button[data-page='ranking']");
  await waitFor(p, "document.querySelector('#m2-root .tab-pane[data-value=ranking]')?.classList.contains('active') && document.querySelector(\"#shell-nav button[data-page='ranking']\")?.getAttribute('aria-current') === 'page'", 20000, "ranking from sidebar");
  check("sidebar: page link opens Module 2's store ranking", /Stockout Risk\s*\/\s*Store ranking/.test(await txt(p, "#m2-root .m2-crumb")));
  await waitFor(p, "document.querySelectorAll('#m2-priority-table tr[data-open-store]').length > 0", 30000, "ranking rows");
  const ranks = await evaluate(p, "[...document.querySelectorAll('#m2-priority-table tr[data-open-store]')].map(r => r.dataset.rank)");
  const unranked = await evaluate(p, "[...document.querySelectorAll('#m2-priority-table td.m2-unranked')].map(td => td.textContent)");
  check("ranking: ranks are numbers or explicitly not ranked", ranks.every((r) => r === "" || /^\d+$/.test(r)) && unranked.every((t) => /Not ranked/.test(t)), JSON.stringify({ ranks: ranks.slice(0, 12), unranked }));

  // Store detail: dates, as-of MSL wording, honest coverage note.
  await click(p, "#m2-priority-table tr[data-open-store] a.m2-store-link");
  await waitFor(p, "!!document.querySelector('#m2_detail') && document.querySelector('#m2-detail-dates')", 30000, "store detail");
  await idle(p);
  const detail = await evaluate(p, "document.querySelector('.tab-pane[data-value=detail]').textContent");
  check("detail: shows the store with its dates", /Data as of/.test(detail) && /Forecast window/.test(detail), detail.slice(0, 300));
  check("detail: MSL is the as-of store value", /MSL \(as of\)/.test(await evaluate(p, "(document.querySelector('#m2-sku-table, #m2-reported-table') || {innerText: ''}).innerText")) || /MSL/.test(detail));
  await click(p, "#m2_detail_tabs a[data-value='oos']");
  await waitFor(p, "!!document.querySelector('#m2-detail-oos-note')", 20000, "store OOS tab");
  await soft(p, "/Days with data/.test(document.querySelector('#m2-root .tab-pane[data-value=oos]')?.innerText || '')", 20000, "store OOS table");
  const note = await txt(p, "#m2-detail-oos-note");
  check("detail: absent days are not counted as unknown", /not counted as missing reports/.test(note) && /Days with data/.test(await evaluate(p, "document.querySelector('#m2-root .tab-pane[data-value=oos]').innerText")), note);
  await shot(p, "3-module2-detail");

  // Analytics: MSL filter applies to bars, SKU choices and the drilldown alike; a bar click selects.
  await evaluate(p, "m2Go('analytics') || true");
  await waitFor(p, "!!document.querySelector('#m2-oos-note')", 30000, "analytics");
  await click(p, "#m2_oos_measure input[value='28d'] + span"); await idle(p);
  await waitFor(p, "/store-days/.test(document.querySelector('#m2-oos-chart-card')?.innerText || '') || !!document.querySelector('#m2-oos-chart-card .m2-state')", 60000, "28-day analytics");
  await click(p, "#m2_oos_msl input[value='MSL'] + span"); await idle(p);
  await waitFor(p, "!!document.querySelector('#m2-oos-filter-note')", 20000, "MSL filter note");
  const option = await evaluate(p, "(() => { const o = [...document.querySelectorAll('#m2_oos_sku option')].find(o => o.value); return o ? {value: o.value, count: Number((o.textContent.match(/\\((\\d+)\\)\\s*$/) || [])[1])} : null; })()");
  if (option) {
    const hasBar = await evaluate(p, "!!(document.getElementById('m2-oos-bar') && document.getElementById('m2-oos-bar').emit)");
    if (hasBar) await evaluate(p, `document.getElementById('m2-oos-bar').emit('plotly_click', {points: [{y: ${JSON.stringify(option.value)}}]}) || true`);
    else await evaluate(p, `(() => { const s = document.querySelector('#m2_oos_sku'); s.value = ${JSON.stringify(option.value)}; s.dispatchEvent(new Event('change', {bubbles: true})); return true; })()`);
    await waitFor(p, `document.querySelector('#m2_oos_sku').value === ${JSON.stringify(option.value)} && !!document.querySelector('#m2-oos-store-table')`, 20000, "bar click selects SKU");
    await idle(p);
    const counts = await evaluate(p, "[...document.querySelectorAll('#m2-oos-store-table tr[data-count]')].map(r => Number(r.dataset.count))");
    check("analytics: bar click selects the SKU", hasBar, "plotly chart present");
    check("analytics: MSL drilldown reconciles with the SKU choice", counts.length > 0 && counts.every((c) => c > 0) && counts.reduce((a, b) => a + b, 0) === option.count, JSON.stringify({ option, counts }));
  } else {
    check("analytics: no MSL out-of-stock SKUs in this synthetic window (nothing to drill)", /No MSL|None reported|No out-of-stock/i.test(await txt(p, "#m2-oos-chart-card")));
  }
  check("analytics: MSL share is labelled as all observations", /not filtered by MSL/.test(await txt(p, "#m2-oos-donut-card")) || !(await visible(p, "#m2-oos-donut-card")));
  await shot(p, "4-module2-analytics");

  // Module 2 -> Module 4 through the top-bar Copilot button; the same source in both.
  await evaluate(p, "window.scrollTo(0, 0) || true");
  await click(p, "#m2_open_copilot");
  await waitFor(p, "document.querySelector('#shell-panel-module_4')?.offsetParent !== null", 20000, "copilot opened from Module 2");
  check("navigation: Module 2 Copilot button opens Module 4", (await evaluate(p, "document.querySelector(\"#shell-nav button[data-module='module_4']\").getAttribute('aria-current')")) === "page");
  check("sidebar: Module 2 page links hidden while Module 4 is open", !(await visible(p, "#shell-nav .ws-subnav[data-module='module_2']")));
  check("Module 4: Module 2 design (workspace kit, Inter, red primary Ask button, breadcrumb)", await evaluate(p, "document.fonts.ready.then(() => getComputedStyle(document.querySelector('#m4-root .ws-title')).fontFamily.includes('Workspace Inter') && getComputedStyle(document.querySelector('#m4_btn_ask')).backgroundColor === 'rgb(180, 24, 36)' && /Commercial Copilot\\s*\\/\\s*Ask/.test(document.querySelector('#m4-root .ws-crumb').textContent) && !document.querySelector('#m4-root .sleek-card, #m4-root .btn-luxury-primary'))"));
  await waitFor(p, "/Data through/.test(document.querySelector('#m4-root .m4-status')?.textContent || '')", 30000, "copilot status");
  const status = await txt(p, "#m4-root .m4-status");
  check("Module 4: same publication and as-of as Module 2", status.includes(`Data through ${EXPECT.asOf}`) && status.includes(EXPECT.artifact), status);
  check("Module 4: model stub is reachable", !/not running/.test(status), status);
  const box = await evaluate(p, "Math.round(document.querySelector('#m4_user_query').getBoundingClientRect().width)");
  check("Module 4: question box is usable (not squeezed by the button)", box > 400, `${box}px`);
  await ask(p, DATA_Q);
  const data = await answered(p, DATA_Q);
  const turn = `[...document.querySelectorAll('#m4-root .m4-turn')].find(t => t.querySelector('.m4-q')?.textContent === ${JSON.stringify(DATA_Q)})`;
  const tabs = await evaluate(p, `[...${turn}.querySelectorAll('[data-m4-tab]')].map(t => t.dataset.m4Tab + ':' + t.getAttribute('aria-selected')).join(',')`);
  check("Module 4 design: home hidden after the first question; answer has Chart | Table | SQL tabs, chart first", !(await visible(p, "#m4-home")) && tabs === "chart:true,table:false,sql:false", tabs);
  await evaluate(p, `${turn}.querySelector('[data-m4-tab=table]').click() || true`);
  check("Module 4 design: the Table tab shows the table and hides the chart", await evaluate(p, `(() => { const t = ${turn}; return t.querySelector('[data-m4-panel=table]').offsetParent !== null && t.querySelector('[data-m4-panel=chart]').hidden; })()`));
  check("Module 4 design: composer fixed at the bottom; data-scope rail beside the conversation", await evaluate(p, "getComputedStyle(document.querySelector('#m4-root .m4-composer-wrap')).position === 'sticky' && document.querySelector('#m4-rail').open && document.querySelector('#m4-rail').getBoundingClientRect().left > document.querySelector('#m4-root .m4-main').getBoundingClientRect().right"));
  check("Charts: multi-hue palette, not brand red", await evaluate(p, `(() => { const c = ${turn}.querySelector('.m4-chart .js-plotly-plot'); const fills = c ? [...c.querySelectorAll('.point path, .bars path')].map(e => e.style.fill) : []; return fills.length > 0 && fills.every(f => f !== 'rgb(180, 24, 36)'); })()`));
  check("Module 4: data answer within the manager's scope", data.table && data.provenance.includes(EXPECT.artifact) && data.provenance.includes(`all ${EXPECT.manager} stores`), data.provenance);
  check("Module 4: business-data answer carries no prediction provenance", !/predictions:|scoring run/i.test(data.provenance) && !data.note, data.provenance);
  check("Module 4: status shows released synthetic predictions", /Predictions: synthetic demo · run score-/.test(status), status);
  await ask(p, RISK_Q);
  const risk = await answered(p, RISK_Q);
  check("Module 4: stockout-alert answer reads published predictions", risk.table && risk.columns.includes("predicted_oos_probability") && risk.provenance.includes("predictions: sku_risk") && RUN.test(risk.provenance), risk.provenance);
  check("Module 4: alert answer discloses synthetic status, run and window", /Synthetic demo predictions/.test(risk.note) && /\(run score-\d{4}-\d{2}-\d{2}-\w+, model /.test(risk.note) && /for \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}/.test(risk.note), risk.note);
  await predictionChecks(p, "Module 4 manager", EXPECT.manager, /Synthetic demo predictions/);
  await ask(p, DOC_Q);
  const doc = await answered(p, DOC_Q);
  check("Module 4: knowledge answer cites documents", doc.cites || doc.passages > 0, doc.text.slice(0, 200));
  await shot(p, "5-module4-manager");

  // Back to Module 2: its state is still there.
  await openModule(p, "module_2");
  check("navigation: back to Module 2 keeps its page", await soft(p, "document.querySelector('#m2-root .tab-pane[data-value=analytics]')?.classList.contains('active')", 10000, "analytics kept"));

  // Identity switch while an answer is running: the answer never reaches the next user.
  await openModule(p, "module_4");
  const slow = "SLOWANSWER How many units were sold in each channel?";
  await ask(p, slow);
  await waitFor(p, "!!document.querySelector('#m4-root .m4-thinking')", 15000, "answer running");
  await signOut(p);
  check("sign-out: workspace content hidden", !(await visible(p, "#shell-app")));
  await signIn(p, "rep_a", "demo:rep-a");
  await moduleTwoChecks(p, "rep A", EXPECT.rep);
  await openModule(p, "module_4");
  check("identity switch: rep A can ask at once (previous question cancelled)",
    await soft(p, "!document.querySelector('#m4_btn_ask').disabled && document.querySelector('#m4_btn_ask bslib-switch-inline')?.getAttribute('case') === 'ready'", 5000, "ask free"));
  await sleep(14000);                                  // past the stub's full delay (two 6 s model calls)
  await idle(p);
  const repView = await evaluate(p, "document.querySelector('#m4-root').innerText");
  check("identity switch: the manager's question and answer are not shown to rep A", !/SLOWANSWER/.test(repView) && !/STUB ANSWER/.test(repView), repView.slice(0, 300));
  const repStatus = await txt(p, "#m4-root .m4-status");
  check("rep A: copilot uses the same release", repStatus.includes(EXPECT.artifact), repStatus);
  await ask(p, DATA_Q);
  const repData = await answered(p, DATA_Q);
  check("rep A: data answer limited to the rep's stores", repData.provenance.includes(`your ${EXPECT.rep} assigned stores`), repData.provenance);
  await predictionChecks(p, "rep A", EXPECT.rep, /Synthetic demo predictions/);
  await shot(p, "6-module4-rep");

  // Business Performance: the rep's scope, rep view; then an identity switch clears it and loads the manager's.
  await moduleOneChecks(p, "rep A", EXPECT.rep, false);
  await shot(p, "7-module1-rep");
  await moduleThreeChecks(p, "rep A", EXPECT.rep, false);
  await watchWelcome(p);
  await signIn(p, "manager", "demo:manager");
  await moduleOneReady(p);
  check("identity switch: Module 1 reloads for the new identity (welcome shown, not the rep's figures)", (await evaluate(p, "(window.__m1Welcome || []).some((g) => /, Manager\\b/.test(g))")) && !(await evaluate(p, "(window.__m1Welcome || []).some((g) => /Rep A/.test(g))")));
  await moduleOneChecks(p, "manager", EXPECT.manager, true);
  await filterPillChecks(p);
  await kpiDetailChecks(p);
  await idle(p);                                       // the page links re-render after each page change
  await waitFor(p, "document.querySelector(\"#shell-nav .ws-subnav[data-module='module_1'] button[data-page='shelf']\")?.offsetParent !== null", 20000, "module 1 page link visible");
  await click(p, "#shell-nav .ws-subnav[data-module='module_1'] button[data-page='shelf']");
  await waitFor(p, "/Shelf/.test(document.querySelector('#m1_crumb')?.innerText || '')", 20000, "module 1 page link");
  check("sidebar: Module 1 page links open the page", /Shelf execution/.test(await txt(p, "#m1_crumb")));
  await shot(p, "8-module1-manager-shelf");

  // One top bar across modules: Module 2's Copilot button and placeholder icons, then the user.
  const barIcons = (root) => `[...document.querySelectorAll('${root} .ws-topbar .ws-icon-btn[aria-disabled="true"], ${root} .m2-topbar .m2-icon-btn[aria-disabled="true"]')].map(b => b.getAttribute('aria-label')).join('|')`;
  const placeholders = "Messages — coming soon|Notifications — coming soon|Calendar — coming soon";
  check("top bar: Module 1 has Module 2's message, notification and calendar icons", (await evaluate(p, barIcons("#m1-root"))) === placeholders);
  await click(p, "#m1_open_copilot");
  await waitFor(p, "document.querySelector('#shell-panel-module_4')?.offsetParent !== null", 20000, "copilot opened from Module 1");
  check("top bar: Module 1's Copilot button opens the Commercial Copilot", await evaluate(p, "document.querySelector(\"#shell-nav button[data-module='module_4']\").getAttribute('aria-current') === 'page'"));
  check("top bar: Module 4 has the same icons and no Copilot button of its own", (await evaluate(p, barIcons("#m4-root"))) === placeholders && !(await evaluate(p, "!!document.querySelector('#m4-root .ws-copilot')")));

  // Negotiation Simulator (Module 3) for the manager: the full deal and price-change flow; then one sign-out.
  await moduleThreeChecks(p, "manager", EXPECT.manager, true);
  await signOut(p);
  check("sidebar sign-out ends the session for every module", !(await visible(p, "#shell-app")));

  // Overview additions: the rep's visit plan, the manager's territory view, questions that open the Copilot.
  const a = await openPage({ name: "additions", w: 1440, h: 900 });
  await signIn(a, "manager", "demo:manager");
  await moduleTwoReady(a);
  await waitFor(a, "document.querySelectorAll('#m2-channel-table tbody tr').length > 0", 30000, "territory view");
  check("manager: sales at risk by region chart and channel table", await visible(a, "#m2-region-risk") && await visible(a, "#m2-channel-table"));
  check("manager: territory overview shows current MSL gaps", /Current MSL gaps/.test(await txt(a, "#m2-region-table")));
  await signIn(a, "rep_a", "demo:rep-a");
  await moduleTwoReady(a);
  await waitFor(a, "document.querySelectorAll('#m2-top10-table tr[data-open-store]').length > 0", 30000, "visit plan");
  check("rep: visit plan heading and What to do column", /Your visit plan/.test(await txt(a, "#m2-top10")) && /What to do/.test(await txt(a, "#m2-top10-table")));
  check("rep: no territory view", !(await visible(a, "#m2-regions")));
  const askAlerts = "How many stockout alerts are there in my stores?";
  await click(a, "#m2-ask .m2-ask-item:nth-child(2)");
  await waitFor(a, "document.querySelector('#shell-panel-module_4')?.offsetParent !== null", 20000, "copilot opened by a question");
  const asked = await answered(a, askAlerts);
  check("rep: an overview question opens the Copilot and is answered from the predictions", /not an observed stockout/.test(asked.note), asked.note);
  await openModule(a, "module_2");
  const store = await evaluate(a, "document.querySelector('#m2-top10-table tr[data-open-store]').dataset.store");
  await click(a, "#m2-top10-table tr[data-open-store] a.m2-store-link");
  await waitFor(a, "!!document.querySelector('#m2-ask-store') && document.querySelector('#m2-ask-store').offsetParent !== null", 30000, "store page button");
  await click(a, "#m2-ask-store");
  await waitFor(a, "document.querySelector('#shell-panel-module_4')?.offsetParent !== null", 20000, "copilot opened from a store");
  const restock = await answered(a, `Which SKU should I restock first in store ${store}?`);
  check("rep: the store button asks about that store from the predictions", /not an observed stockout/.test(restock.note), restock.note);
  await shot(a, "6-additions");

  // Smaller screens: the shell bar wraps; record any horizontal overflow.
  for (const vp of [{ name: "tablet", w: 768, h: 1024 }, { name: "phone", w: 390, h: 844, mobile: true }]) {
    const q = await openPage(vp);
    await signIn(q, "rep_b", "demo:rep-b");
    await moduleTwoReady(q);
    const over = await overflow(q);
    check(`${vp.name}: no horizontal page overflow`, over <= 1, `${over}px`);
    await shot(q, "module2");
    await openModule(q, "module_4");
    await shot(q, "module4");
    await moduleOneChecks(q, `${vp.name} rep B`, null, false);
    const m1over = await overflow(q);
    check(`${vp.name}: Business Performance without horizontal page overflow`, m1over <= 1, `${m1over}px`);
    await moduleThreeChecks(q, `${vp.name} rep B`, null, false);
    const m3over = await overflow(q);
    check(`${vp.name}: Negotiation Simulator without horizontal page overflow`, m3over <= 1, `${m3over}px`);
    await shot(q, "module3");
    await shot(q, "module1");
  }
}

async function scenarioUnavailable() {
  const p = await openPage({ name: "desktop", w: 1280, h: 860 });
  await signIn(p, "manager", "demo:manager");
  await waitFor(p, "!!document.querySelector('#shell-alert')", 20000, "unavailable alert");
  const alert = await txt(p, "#shell-alert");
  check("unavailable: actionable shell alert", /unavailable/i.test(alert) && /SHARED_DATA_PUBLICATION/.test(alert) && /docs\/integrated-app\.md/.test(alert), alert);
  check("unavailable: source chip says unavailable", (await evaluate(p, "document.querySelector('#shell-source').dataset.ready")) === "no");
  check("unavailable: no path or credential in the alert", !/\/tmp|\/Users|password|postgresql:/.test(alert), alert);
  await openModule(p, "module_2");
  await waitFor(p, "!!document.querySelector('#m2-root .m2-banner.bad')", 30000, "Module 2 unavailable banner");
  check("unavailable: Module 2 shows unavailable, not data", /Unavailable/.test(await txt(p, "#m2-root .m2-banner.bad")) && !(await evaluate(p, "!!document.querySelector('#m2-top10-table tr[data-open-store]')")));
  await shot(p, "1-module2");
  await openModule(p, "module_1");
  await moduleOneReady(p);
  check("unavailable: Business Performance shows unavailable, not data", /unavailable/i.test(await txt(p, "#m1-unavailable")) && !(await evaluate(p, "!!document.querySelector('#m1-kpis')")), await txt(p, "#m1-unavailable"));
  await openModule(p, "module_3");
  await waitFor(p, "!!document.querySelector('#m3-unavailable')", 60000, "module 3 unavailable");
  check("unavailable: Negotiation Simulator shows unavailable, not data", /unavailable/i.test(await txt(p, "#m3-unavailable")) && !(await evaluate(p, "!!document.querySelector('#m3-context')")), await txt(p, "#m3-unavailable"));
  await openModule(p, "module_4");
  const status = await txt(p, "#m4-root .m4-status");
  check("unavailable: Module 4 data chip explains", /Shared business data unavailable/.test(status), status);
  await ask(p, DATA_Q);
  const data = await answered(p, DATA_Q);
  check("unavailable: data question is refused, no table", data.warn && !data.table, data.text.slice(0, 200));
  await ask(p, DOC_Q);
  const doc = await answered(p, DOC_Q);
  check("unavailable: knowledge question still answered with sources", doc.cites || doc.passages > 0, doc.text.slice(0, 200));
  await shot(p, "2-module4");
}

async function scenarioNoModel() {
  const p = await openPage({ name: "desktop", w: 1280, h: 860 });
  await signIn(p, "rep_a", "demo:rep-a");
  await moduleTwoChecks(p, "no model", EXPECT.rep);
  await openModule(p, "module_4");
  await waitFor(p, "/Model:/.test(document.querySelector('#m4-root .m4-status')?.textContent || '')", 20000, "status");
  check("no model: status says the model is not running", /not running/.test(await txt(p, "#m4-root .m4-status")));
  await ask(p, DATA_Q);
  const out = await answered(p, DATA_Q);
  check("no model: clear model-unavailable answer", out.warn && /not running/.test(out.text), out.text.slice(0, 200));
  await shot(p, "1-module4");
  await openModule(p, "module_2");
  check("no model: Module 2 and the session keep working", await soft(p, "document.querySelectorAll('#m2-top10-table tr[data-open-store]').length > 0", 20000, "rows"));
}

async function scenarioCandidate() {
  const p = await openPage({ name: "desktop", w: 1440, h: 900 });
  await signIn(p, "manager", "demo:manager");
  await moduleTwoReady(p);
  await waitFor(p, "!!document.querySelector('#m2-model-status')", 20000, "candidate label");
  check("candidate: overview label", /review candidate, not an approved production model/.test(await txt(p, "#m2-model-status")));
  check("candidate: banner separates predictions from estimates", /predictions from a model under review/.test(await txt(p, "#m2-estimate-note")) && /estimates/.test(await txt(p, "#m2-estimate-note")));
  await evaluate(p, "m2Go('analytics') || true");
  await waitFor(p, "!!document.querySelector('#m2-oos-note')", 30000, "analytics");
  check("candidate: analytics label", await visible(p, "#m2-model-status"));
  await evaluate(p, "m2Go('overview') || true");
  await waitFor(p, "document.querySelector('#m2-root .tab-pane[data-value=overview]')?.classList.contains('active') && document.querySelectorAll('#m2-top10-table tr[data-open-store]').length > 0", 30000, "overview rows");
  await idle(p); await sleep(300);
  await click(p, "#m2-top10-table tr[data-open-store] a.m2-store-link");
  await waitFor(p, "!!document.querySelector('#m2-detail-model-status')", 30000, "detail label");
  check("candidate: store detail label", /review candidate/.test(await txt(p, "#m2-detail-model-status")));
  check("candidate: store detail banner", await visible(p, "#m2-estimate-note"));
  await shot(p, "1-detail");
  await openModule(p, "module_4");
  await waitFor(p, "/Data through/.test(document.querySelector('#m4-root .m4-status')?.textContent || '')", 30000, "copilot status");
  await predictionChecks(p, "candidate Module 4", EXPECT.manager, /Model: review candidate, not an approved production model\./);
  await shot(p, "2-module4-candidate");
}

async function scenarioPg() {
  const p = await openPage({ name: "desktop", w: 1280, h: 860 });
  await signIn(p, "manager", "demo:manager");
  check("pg: shared PostgreSQL source", /Shared PostgreSQL/.test(await txt(p, "#shell-source")));
  await moduleTwoChecks(p, "pg manager", EXPECT.manager);
  await signIn(p, "rep_a", "demo:rep-a");
  await moduleTwoChecks(p, "pg rep A", EXPECT.rep);
  await openModule(p, "module_4");
  await ask(p, DATA_Q);
  const out = await answered(p, DATA_Q, 120000);
  check("pg: rep A copilot answer in scope", out.table && out.provenance.includes(`your ${EXPECT.rep} assigned stores`), out.provenance);
  await predictionChecks(p, "pg rep A", EXPECT.rep, /Synthetic demo predictions/);
  await shot(p, "1-module4");
  await moduleOneChecks(p, "pg rep A", EXPECT.rep, false);
  await shot(p, "2-module1");
}

async function scenarioPgRevoked() {
  const p = await openPage({ name: "desktop", w: 1280, h: 860 });
  await signIn(p, "rep_a", "demo:rep-a");
  await openModule(p, "module_2");
  await waitFor(p, "/No access/.test(document.querySelector('#m2-root')?.innerText || '')", 30000, "Module 2 no access");
  check("revoked: Module 2 shows no access and no rows", !(await evaluate(p, "!!document.querySelector('#m2-top10-table tr[data-open-store]')")));
  await openModule(p, "module_4");
  await ask(p, DATA_Q);
  const out = await answered(p, DATA_Q, 60000);
  check("revoked: copilot refuses data", out.warn && !out.table, out.text.slice(0, 200));
  await ask(p, STORED_Q);
  const ranking = await answered(p, STORED_Q, 60000);
  check("revoked: copilot refuses the ranking question", ranking.warn && !ranking.table && !ranking.note, ranking.text.slice(0, 200));
  await openModule(p, "module_1");
  await moduleOneReady(p);
  check("revoked: Business Performance shows no data scope and no KPIs", /no data scope/.test(await txt(p, "#m1-unavailable")) && !(await evaluate(p, "!!document.querySelector('#m1-kpis')")), await txt(p, "#m1-unavailable"));
  await openModule(p, "module_3");
  await waitFor(p, "!!document.querySelector('#m3-unavailable')", 60000, "module 3 no scope");
  check("revoked: Negotiation Simulator shows no data scope and no account", /no data scope/.test(await txt(p, "#m3-unavailable")) && !(await evaluate(p, "!!document.querySelector('#m3-context')")), await txt(p, "#m3-unavailable"));
  await signIn(p, "manager", "demo:manager");
  await moduleTwoChecks(p, "revoked: manager unaffected", EXPECT.manager);
  await shot(p, "1-manager");
}

async function scenarioUnreleased() {
  const p = await openPage({ name: "desktop", w: 1280, h: 860 });
  await signIn(p, "rep_a", "demo:rep-a");
  await openModule(p, "module_4");
  await waitFor(p, "/Data through/.test(document.querySelector('#m4-root .m4-status')?.textContent || '')", 30000, "copilot status");
  const status = await txt(p, "#m4-root .m4-status");
  check("unreleased: status says predictions are not released", /Predictions: not released/.test(status), status);
  for (const q of [RISK_Q, STORED_Q]) {
    await ask(p, q);
    const out = await answered(p, q);
    check(`unreleased: "${q.slice(0, 30)}" is explicitly unavailable, not zero risk`, out.warn && !out.table && /not released/.test(out.text) && /not zero risk/.test(out.text) && !/show the sql/i.test(out.text), out.text.slice(0, 300));
  }
  await ask(p, DATA_Q);
  const data = await answered(p, DATA_Q);
  check("unreleased: business data questions still answered", data.table && data.provenance.includes(`your ${EXPECT.rep} assigned stores`) && !data.note, data.provenance);
  await shot(p, "1-module4");
}

const SCENARIOS = { main: scenarioMain, unavailable: scenarioUnavailable, nomodel: scenarioNoModel,
  candidate: scenarioCandidate, pg: scenarioPg, pgrevoked: scenarioPgRevoked, unreleased: scenarioUnreleased };
try {
  await SCENARIOS[SCENARIO]();
} catch (error) {
  check("scenario completed", false, error.stack || String(error));
  try { if (globalThis.lastShot) await shot(globalThis.lastShot, "error"); } catch (e) { /* ignore */ }
}
results.passed = results.checks.filter((c) => c.ok).length;
results.failed = results.checks.filter((c) => !c.ok).length;
results.finished_at = new Date().toISOString();
writeFileSync(join(OUT, `${SCENARIO}-results.json`), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ scenario: SCENARIO, passed: results.passed, failed: results.failed }));
ws.close(); chrome.kill(); await sleep(300);
try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
process.exit(results.failed ? 1 : 0);
