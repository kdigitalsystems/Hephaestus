const fs = require("fs");
const vm = require("vm");

const appSource = fs.readFileSync("docs/app.js", "utf8");
const htmlSource = fs.readFileSync("docs/index.html", "utf8");
const elements = new Map();

["Avg Confidence", "Last Verified", "Review Queue", "Approved", "Rejected", "Morning Brief", "Investor Radar", "Investment Radar", "Decision Support"].forEach((label) => {
  if (htmlSource.includes(label)) {
    throw new Error(`public dashboard still exposes redundant/developer label: ${label}`);
  }
});

function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      id,
      value: "",
      checked: false,
      textContent: "",
      classList: { add() {}, remove() {}, toggle() {} },
      appendChild() {},
      querySelector() { return null; },
    });
  }
  return elements.get(id);
}

const context = {
  console,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  fetch: () => new Promise(() => {}),
  document: {
    createElement: (tag) => ({
      tagName: tag,
      appendChild() {},
      className: "",
      textContent: "",
      value: "",
      checked: false,
      style: {},
      classList: { add() {}, remove() {}, toggle() {} },
    }),
    getElementById: element,
    querySelectorAll: () => [],
  },
  history: {
    pushed: [],
    replaced: [],
    pushState(_state, _title, hash) { this.pushed.push(hash); context.window.location.hash = hash; },
    replaceState(_state, _title, hash) { this.replaced.push(hash); context.window.location.hash = hash; },
  },
  window: {
    location: { hash: "" },
    addEventListener() {},
    scrollTo() {},
  },
};
context.window.window = context.window;
context.window.document = context.document;
context.window.history = context.history;
context.globalThis = context;

vm.createContext(context);
vm.runInContext(appSource, context);

const roundTrip = context.hashToRoute(context.routeToHash({
  view: "companies",
  query: "tsm",
  sector: "Technology",
  connected: "1",
}));

if (roundTrip.view !== "companies" || roundTrip.query !== "tsm" || roundTrip.sector !== "Technology" || roundTrip.connected !== "1") {
  throw new Error(`route round-trip failed: ${JSON.stringify(roundTrip)}`);
}

vm.runInContext(`
  allCompanies = [
    {
      name: "Advanced Micro Devices",
      ticker: "AMD",
      sector: "Technology",
      industry: "Semiconductors",
      connection_count: 1,
      upstream: [{ ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", confidence: 0.95, review_status: "approved" }],
      downstream: [],
    },
    {
      name: "Acme Retail",
      ticker: "ACME",
      sector: "Consumer",
      industry: "Retail",
      connection_count: 1,
      upstream: [{ ticker: "PRIVATE", name: "Private Vendor", type: "Logistics", confidence: 0.6, review_status: "pending" }],
      downstream: [],
    },
  ];
`, context);

element("search-input").value = "taiwan";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = true;

vm.runInContext("applyFilters(false); globalThis.__filteredTickers = currentCompaniesList.map(company => company.ticker);", context);

if (context.__filteredTickers.length !== 1 || context.__filteredTickers[0] !== "AMD") {
  throw new Error(`expected richer search/filter to match AMD only, got ${JSON.stringify(context.__filteredTickers)}`);
}
