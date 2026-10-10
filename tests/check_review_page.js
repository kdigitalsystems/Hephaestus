// The review page's decision files must be exactly what apply_human_review.py accepts.
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { spawnSync } = require("child_process");

const source = fs.readFileSync("docs/review.js", "utf8");
const html = fs.readFileSync("docs/review.html", "utf8");
const context = { console, URLSearchParams, encodeURIComponent, Date, JSON, Math, Number, String, Map, Object, Array };
vm.createContext(context);
vm.runInContext(source, context); // no #review-app element here, so the page does not start
const run = (expression) => vm.runInContext(expression, context);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// The page's controls exist and nothing is rendered through innerHTML.
["review-list", "review-filters", "review-search", "review-order", "review-open-pr", "review-download", "review-copy", "review-accept-suggestions", "review-submit"]
  .forEach((id) => assert(html.includes(`id="${id}"`), `review.html is missing #${id}`));
assert(!/innerHTML|insertAdjacentHTML|document\.write/.test(source), "review.js must build the DOM with textContent only");
assert(run("uploadUrl()") === "https://github.com/kdigitalsystems/Hephaestus/upload/main/data/human_review", "large sessions upload one file");
assert(html.includes('name="robots" content="noindex'), "the review page must not be indexed");

const item = (id, action) => ({ edge_id: id, source_ticker: "AAPL", target_ticker: "GLW", type: "Cover glass", action });
const now = new Date(Date.UTC(2026, 8, 29, 4, 5, 6));
context.__now = now;

const record = run(`decisionRecord({ edge_id: "12", source_ticker: "AAPL", target_ticker: "GLW", type: "Cover glass" }, { action: "reverse", note: "  checked  " })`);
assert(record.edge_id === 12 && record.action === "reverse" && record.note === "checked", `bad record ${JSON.stringify(record)}`);
assert(!("note" in run(`decisionRecord({ edge_id: 1, source_ticker: "A", target_ticker: "B", type: "T" }, { action: "approve", note: "" })`)), "empty notes are omitted");

context.__records = [item(30, "reject"), item(4, "approve"), item(17, "reverse")];
const file = run("buildDecisionFile(__records, __now)");
assert(file.reviewed_on === "2026-09-29", `reviewed_on ${file.reviewed_on}`);
assert(file.decisions.map((d) => d.edge_id).join(",") === "4,17,30", "decisions are sorted by edge id");

const single = run("pullRequestLinks(__records, __now)");
assert(single.length === 1, "a small session is one file");
assert(single[0].path === "data/human_review/review-20260929-040506.json", `path ${single[0].path}`);
assert(single[0].url.startsWith("https://github.com/kdigitalsystems/Hephaestus/new/main?filename=data%2Fhuman_review%2F"), single[0].url.slice(0, 90));
assert(JSON.parse(decodeURIComponent(single[0].url.split("&value=")[1])).decisions.length === 3, "the URL carries the file content");

// A long session is split so every link fits GitHub's URL limit, and nothing is lost.
context.__many = Array.from({ length: 400 }, (_, index) => item(index + 1, ["approve", "reverse", "reject"][index % 3]));
const parts = run("pullRequestLinks(__many, __now)");
assert(parts.length > 1, "400 decisions need several files");
assert(parts.every((part) => part.url.length <= 6000), "every link stays under GitHub's measured ~6,900-character limit");
assert(parts.reduce((total, part) => total + part.count, 0) === 400, "every decision is in exactly one file");
assert(new Set(parts.map((part) => part.path)).size === parts.length, "each part has its own file name");

// The dashboard's own queue works before review_queue.json exists.
const fallback = run(`normalizeQueue({ quality: { pending_count: 300, review_queue: [
  { edge_id: 1, review_note: "Ollama consensus review left pending: Evidence excerpt does not name both companies; held for human review." },
  { edge_id: 2, review_note: "" } ] } })`);
assert(fallback.items[0].category === "unnamed" && fallback.items[1].category === "awaiting_models", "fallback categories");
assert(fallback.truncated && !fallback.withSuggestions, "fallback reports truncation and no suggestions");
assert(run(`matchesSearch({ source_ticker: "AAPL", source_name: "Apple Inc.", target_ticker: "GLW", target_name: "Corning" }, "corn")`), "search matches names");

// Largest company first by default; the dashboard's fallback queue has no sizes and keeps id order.
context.__sized = [
  { edge_id: 5, source_market_cap: 5e10, target_market_cap: 1.4e11 },
  { edge_id: 9, source_market_cap: 4e10, target_market_cap: 3.5e12 },
  { edge_id: 2, source_market_cap: null, target_market_cap: 6e10 },
  { edge_id: 7 },
  { edge_id: 3 },
];
const order = (name) => run(`sortItems(__sized, "${name}")`).map((entry) => entry.edge_id).join(",");
assert(order("size") === "9,5,2,3,7", `size order ${order("size")}`);
assert(order("oldest") === "2,3,5,7,9" && order("newest") === "9,7,5,3,2", "age orders");
assert(run("__sized.map(entry => entry.edge_id).join(',')") === "5,9,2,7,3", "sorting does not reorder the queue itself");
assert(run("formatMarketCap(3.5e12)") === "$3.5T" && run("formatMarketCap(1.4e11)") === "$140B" && run("formatMarketCap(2.5e8)") === "$250M", "market caps read as $3.5T / $140B / $250M");
assert(run("formatMarketCap(null)") === "" && run("formatMarketCap(0)") === "", "no size, no label");

// The pipeline's validator accepts every file the page writes.
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "human-review-"));
parts.forEach((part) => fs.writeFileSync(path.join(directory, path.basename(part.path)), part.content));
fs.writeFileSync(path.join(directory, path.basename(single[0].path)), single[0].content);
const check = spawnSync("python3", ["backend/apply_human_review.py", "--check", "--dir", directory], { encoding: "utf8" });
assert(check.status === 0, `apply_human_review.py rejected the page's files:\n${check.stdout}${check.stderr}`);

// --- The page itself, driven against a small DOM ---------------------------------------
// startReviewPage() runs against recorded fetches and a shared localStorage, so the
// data-loss, shortcut, control and two-tab fixes are exercised the way a visitor hits them.
const STORAGE_KEY = "hephaestus_review_v1";

function mockNode(tag, ownerDocument) {
  const node = {
    tagName: String(tag).toUpperCase(), className: "", textContent: "", children: [], listeners: {}, dataset: {}, attributes: {},
    id: "", value: "", checked: false, hidden: false, disabled: false, ownerDocument,
    appendChild(child) {
      if (child.isFragment) child.children.slice().forEach((inner) => node.appendChild(inner));
      else node.children.push(child);
      return child;
    },
    replaceChildren(...nodes) { node.children = []; nodes.forEach((child) => node.appendChild(child)); },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    addEventListener(type, handler) { (node.listeners[type] = node.listeners[type] || []).push(handler); },
    focus() { ownerDocument.activeElement = node; },
    scrollIntoView() {},
  };
  return node;
}

function textOf(node) { return [node.textContent, ...node.children.map(textOf)].join(""); }
function findAll(node, predicate, found = []) {
  node.children.forEach((child) => { if (predicate(child)) found.push(child); findAll(child, predicate, found); });
  return found;
}

function startPage({ files, storage = new Map(), controls = {} }) {
  const statics = {};
  const documentHandlers = {};
  const windowHandlers = {};
  let booted = false;
  const doc = {
    activeElement: null,
    createElement: (tag) => mockNode(tag, doc),
    createDocumentFragment: () => Object.assign(mockNode("fragment", doc), { isFragment: true }),
    createTextNode: (text) => Object.assign(mockNode("#text", doc), { textContent: text }),
    getElementById(id) {
      if (id === "review-app") return booted ? statics[id] : null;
      if (/^edge-\d+$/.test(id)) return statics["review-list"].children.find((card) => card.id === id) || null;
      if (!statics[id]) statics[id] = Object.assign(mockNode("div", doc), { id });
      return statics[id];
    },
    querySelectorAll: (selector) => (selector === ".rq-card" ? statics["review-list"].children : []),
    addEventListener(type, handler) { (documentHandlers[type] = documentHandlers[type] || []).push(handler); },
  };
  Object.entries(controls).forEach(([id, props]) => Object.assign(doc.getElementById(id), props));
  const respond = (url) => {
    const file = files[url];
    if (!file) return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error("no body")) });
    return Promise.resolve({ ok: (file.status || 200) < 400, status: file.status || 200, json: () => new Promise((resolve) => resolve(JSON.parse(file.body))) });
  };
  const win = {
    scrollY: 0, scrollTo() {}, confirm: () => true, open() {},
    addEventListener(type, handler) { (windowHandlers[type] = windowHandlers[type] || []).push(handler); },
  };
  const ctx = {
    console, document: doc, window: win, fetch: respond, URLSearchParams, encodeURIComponent, setTimeout,
    localStorage: { getItem: (key) => (storage.has(key) ? storage.get(key) : null), setItem: (key, value) => { storage.set(key, String(value)); } },
  };
  vm.createContext(ctx);
  vm.runInContext(source, ctx); // #review-app is absent while loading, so the page waits for startReviewPage() below
  booted = true;
  statics["review-app"] = mockNode("main", doc);
  const page = {
    storage, doc, ctx, statics,
    run: (expression) => vm.runInContext(expression, ctx),
    stored: () => JSON.parse(storage.get(STORAGE_KEY) || "{}"),
    key: (key) => {
      const event = { key, preventDefault() {}, ctrlKey: false, metaKey: false, altKey: false };
      documentHandlers.keydown.forEach((handler) => handler(event));
    },
    storageEvent: () => windowHandlers.storage.forEach((handler) => handler({ key: STORAGE_KEY })),
    card: (id) => statics["review-list"].children.find((card) => card.id === `edge-${id}`),
    click: (node) => node.listeners.click.forEach((handler) => handler({})),
    chip: (label) => findAll(statics["review-filters"], (node) => node.className === "rq-chip" && textOf(node).startsWith(label))[0],
    action: (id, action) => findAll(page.card(id), (node) => node.className.includes(`action-${action}`))[0],
  };
  page.started = vm.runInContext("startReviewPage()", ctx);
  return page;
}

const queueItem = (id, category) => ({ edge_id: id, source_ticker: `S${id}`, target_ticker: `T${id}`, type: "Part", category });
const fullQueue = (items, extra = {}) => ({ status: 200, body: JSON.stringify({ generated_at: "2026-10-08T09:00:00+00:00", pending_count: items.length, truncated: false, categories: { a: "Cat A", b: "Cat B" }, items, ...extra }) });
const liteQueue = (items, pending) => ({ status: 200, body: JSON.stringify({ quality: { pending_count: pending, review_queue: items.map((entry) => ({ ...entry, review_note: "" })) } }) });
const saved = (id, extra = {}) => ({ action: "approve", signature: `S${id}->T${id}:Part`, decided_at: "2026-10-08T10:00:00.000Z", ...extra });
const seed = (decisions) => new Map([[STORAGE_KEY, JSON.stringify({ decisions, sent: {} })]]);
const storedIds = (page) => Object.keys(page.stored().decisions).join();

async function checkPageBehaviour() {
  // 1. Data loss: the dashboard's shorter queue (or a bad review_queue.json) must not erase saved decisions.
  const everyItem = Array.from({ length: 8 }, (_, index) => queueItem(index + 1, "a"));
  const shortList = liteQueue(everyItem.slice(0, 3), 8);
  const decisions = { 1: saved(1), 2: saved(2), 6: saved(6), 7: saved(7, { action: "reject" }), 8: saved(8) };
  const failures = {
    "HTTP 503": { status: 503, body: "unavailable" },
    "malformed JSON": { status: 200, body: '{"items": [' },
    "a body without items": { status: 200, body: "{}" },
  };
  for (const [name, bad] of Object.entries(failures)) {
    const page = startPage({ files: { "review_queue.json": bad, "dashboard_lite.json": shortList }, storage: seed(decisions) });
    await page.started;
    assert(page.run("reviewState.queue.items.length") === 3, `${name}: the fallback queue is in use`);
    assert(storedIds(page) === "1,2,6,7,8", `${name}: saved decisions survive the fallback queue, got ${storedIds(page)}`);
    assert(Object.keys(page.run("reviewState.decisions")).length === 5, `${name}: decisions stay in memory`);
    const warning = page.statics["review-warning"];
    assert(warning.hidden === false && /shorter list/.test(warning.textContent) && /3 of 8/.test(warning.textContent), `${name}: a visible warning names the shorter queue: ${warning.textContent}`);
    assert(/3 saved decisions/.test(warning.textContent), `${name}: the warning says how many saved decisions are not shown: ${warning.textContent}`);
    assert(page.run("records().length") === 2, `${name}: only decisions about links on the page are sent`);
    page.click(page.action(3, "reject"));
    assert(storedIds(page) === "1,2,3,6,7,8", `${name}: deciding on the fallback queue keeps the others, got ${storedIds(page)}`);
  }

  // A fallback that claims to be complete (an older dashboard copy) is still not the queue.
  const older = startPage({ files: { "review_queue.json": failures["HTTP 503"], "dashboard_lite.json": liteQueue(everyItem.slice(0, 3).map((entry) => (entry.edge_id === 2 ? { ...entry, target_ticker: "OTHER" } : entry)), 3) }, storage: seed(decisions) });
  await older.started;
  assert(storedIds(older) === "1,2,6,7,8", `an older dashboard copy must not prune either, got ${storedIds(older)}`);

  // ...while the full queue still drops decisions whose link was settled or is now a different link.
  const changed = everyItem.map((entry) => (entry.edge_id === 2 ? { ...entry, target_ticker: "OTHER" } : entry)).filter((entry) => entry.edge_id !== 6);
  const complete = startPage({ files: { "review_queue.json": fullQueue(changed) }, storage: seed({ 1: saved(1), 2: saved(2), 6: saved(6) }) });
  await complete.started;
  assert(storedIds(complete) === "1", `a settled link and a re-used id are pruned, got ${storedIds(complete)}`);
  assert(complete.statics["review-warning"].hidden === true, "no warning when the full queue loaded");
  const cut = startPage({ files: { "review_queue.json": fullQueue(changed.slice(0, 4), { truncated: true, pending_count: 7 }) }, storage: seed({ 1: saved(1), 2: saved(2), 7: saved(7) }) });
  await cut.started;
  assert(storedIds(cut) === "1,7", `a truncated queue cannot say a missing link is settled, got ${storedIds(cut)}`);

  // 2. Shortcuts act only on a card that is on the page.
  const mixed = [queueItem(1, "a"), queueItem(2, "a"), queueItem(3, "b")];
  const keys = startPage({ files: { "review_queue.json": fullQueue(mixed) } });
  await keys.started;
  keys.run("reviewState.order = 'oldest'; renderList()");
  keys.key("j"); keys.key("j"); keys.key("j");
  assert(keys.run("reviewState.focusId") === 3, "j moves to the third card");
  keys.click(keys.chip("Cat A"));
  assert(!keys.card(3), "the filter hides card 3");
  keys.key("a");
  assert(Object.keys(keys.run("reviewState.decisions")).length === 0, "a must not record a decision for a card that is not shown");
  assert(keys.run("reviewState.focusId") === null, "the filter clears the focus on a hidden card");
  keys.key("j"); keys.key("a");
  assert(Object.keys(keys.run("reviewState.decisions")).join() === "1", "shortcuts still work on a card that is shown");
  keys.run("reviewState.focusId = 3");
  keys.key("x");
  assert(!keys.run("reviewState.decisions")[3], "a stale focus id is ignored even if it was not cleared");

  // 3. Controls show the state the list is in after the browser restores them (Back does).
  const restored = startPage({ files: { "review_queue.json": fullQueue(mixed) }, controls: { "review-hide-decided": { checked: true }, "review-search": { value: "zz" } } });
  await restored.started;
  assert(restored.statics["review-hide-decided"].checked === false && restored.statics["review-search"].value === "", "controls are reset to the list's state");
  assert(restored.statics["review-list"].children.length === 3, "the restored controls do not hide anything");

  // 4. Two tabs: each keeps the other's decisions.
  const shared = new Map();
  const files = { "review_queue.json": fullQueue(mixed) };
  const tabOne = startPage({ files, storage: shared });
  const tabTwo = startPage({ files, storage: shared });
  await tabOne.started; await tabTwo.started;
  tabOne.click(tabOne.action(1, "approve"));
  tabTwo.click(tabTwo.action(2, "reject"));
  assert(storedIds(tabTwo) === "1,2", `a second tab must not erase the first tab's decision, got ${storedIds(tabTwo)}`);
  assert(Object.keys(tabTwo.run("reviewState.decisions")).join() === "1,2", "the second tab now knows both");
  tabOne.storageEvent();
  assert(tabOne.card(2).className.includes("decided-reject"), "the first tab shows the second tab's decision when told of it");
  tabOne.click(tabOne.action(1, "approve")); // toggles #1 off
  tabTwo.click(tabTwo.action(3, "reverse"));
  assert(storedIds(tabTwo) === "2,3", `an undo in one tab holds when the other saves, got ${storedIds(tabTwo)}`);
}

checkPageBehaviour().then(
  () => console.log(`review page checks passed (${parts.length} files validated by apply_human_review.py)`),
  (error) => { console.error(error); process.exit(1); },
);
