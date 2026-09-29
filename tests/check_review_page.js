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
["review-list", "review-filters", "review-search", "review-open-pr", "review-download", "review-copy", "review-accept-suggestions", "review-submit"]
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

// The pipeline's validator accepts every file the page writes.
const directory = fs.mkdtempSync(path.join(os.tmpdir(), "human-review-"));
parts.forEach((part) => fs.writeFileSync(path.join(directory, path.basename(part.path)), part.content));
fs.writeFileSync(path.join(directory, path.basename(single[0].path)), single[0].content);
const check = spawnSync("python3", ["backend/apply_human_review.py", "--check", "--dir", directory], { encoding: "utf8" });
assert(check.status === 0, `apply_human_review.py rejected the page's files:\n${check.stdout}${check.stderr}`);

console.log(`review page checks passed (${parts.length} files validated by apply_human_review.py)`);
