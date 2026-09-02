const fs = require("fs");
const vm = require("vm");

const appSource = fs.readFileSync("docs/app.js", "utf8");
const htmlSource = fs.readFileSync("docs/index.html", "utf8");
const elements = new Map();

["Avg Confidence", "Last Verified", "Review Queue", "Approved", "Rejected", "Morning Brief", "Investor Radar", "Investment Radar", "Decision Support", "20260525-investor3", "20260531-ticker-prefix1", "20260531-ui-fixes2", "20260531-xray-layout1", "20260601-chart-restore1", "20260601-chart-restore2", "20260606-bugfix1", "20260607-deepfix1"].forEach((label) => {
  if (htmlSource.includes(label)) {
    throw new Error(`public dashboard still exposes redundant/developer label: ${label}`);
  }
});

if (!appSource.includes("graph-column") || !appSource.includes("graph-node-list")) {
  throw new Error("X-Ray graph should use stacked columns so many companies do not overlap");
}

if (appSource.includes("graph-line") || appSource.includes("node.style.top") || appSource.includes("node.style.left")) {
  throw new Error("X-Ray graph should not use absolute-positioned node placement");
}

if (!htmlSource.includes("https://s3.tradingview.com/tv.js") || !htmlSource.includes('id="tv_chart_container"')) {
  throw new Error("company detail pages should include the TradingView price chart container");
}

if (!appSource.includes("function renderChart") || !appSource.includes("renderChart(company)") || !appSource.includes("new window.TradingView.widget")) {
  throw new Error("company detail pages should render the price chart when a company opens");
}

if (!appSource.includes("chartRenderToken") || !appSource.includes("renderToken !== chartRenderToken")) {
  throw new Error("chart rendering should ignore stale async readiness checks after navigation");
}

if (!appSource.includes("'Details'") || !appSource.includes("No evidence excerpt was saved for this relationship.")) {
  throw new Error("relationship rows without saved evidence should show an honest details state");
}

if (!htmlSource.includes("overview-stats") || !appSource.includes("function renderOverviewStats")) {
  throw new Error("overview should render a concise data summary before filters");
}

if (!htmlSource.includes('data-nav="predictions"') || !htmlSource.includes('id="view-predictions"')) {
  throw new Error("dashboard should expose the bounded Hephaestus Predictions view");
}

if (!appSource.includes("function renderPredictionsView") || !appSource.includes("fetch('predictions.json')")) {
  throw new Error("prediction view should load and render the published research-signal export");
}

if (!htmlSource.includes("onclick=\"commitSearch()\"") || !appSource.includes("function commitSearch")) {
  throw new Error("ticker search should expose an explicit committed Search action");
}

const htmlIds = new Set([...htmlSource.matchAll(/id="([^"]+)"/g)].map((match) => match[1]));
const appIdRefs = new Set([...appSource.matchAll(/getElementById\('([^']+)'\)/g)].map((match) => match[1]));
const missingIds = [...appIdRefs].filter((id) => !htmlIds.has(id));
if (missingIds.length) {
  throw new Error(`app references missing DOM ids: ${missingIds.join(", ")}`);
}

function mockElement(tag = "div", id = "") {
  const node = {
    id,
    tagName: tag,
    value: "",
    checked: false,
    textContent: "",
    className: "",
    style: {},
    children: [],
    parentNode: null,
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild(child) {
      child.parentNode = node;
      node.children.push(child);
      return child;
    },
    removeChild(child) {
      const index = node.children.indexOf(child);
      if (index >= 0) node.children.splice(index, 1);
      child.parentNode = null;
      return child;
    },
    remove() {
      if (node.parentNode) node.parentNode.removeChild(node);
    },
    insertBefore(child, reference) {
      const index = reference ? node.children.indexOf(reference) : -1;
      child.parentNode = node;
      if (index >= 0) node.children.splice(index, 0, child);
      else node.children.push(child);
      return child;
    },
    attributes: {},
    setAttribute(name, value) { node.attributes[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(node.attributes, name) ? node.attributes[name] : null; },
    querySelector() { return null; },
  };
  Object.defineProperty(node, "firstChild", {
    get() {
      return node.children[0] || null;
    },
  });
  return node;
}

function element(id) {
  if (!elements.has(id)) {
    elements.set(id, mockElement("div", id));
  }
  return elements.get(id);
}

function collectText(node) {
  return [node.textContent, ...(node.children || []).map(collectText)].join("");
}

const context = {
  console,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  fetch: () => new Promise(() => {}),
  document: {
    createElement: (tag) => mockElement(tag),
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
    clearTimeout,
    setTimeout,
    scrollTo() {},
  },
};
context.window.window = context.window;
context.window.document = context.document;
context.window.history = context.history;
context.globalThis = context;

vm.createContext(context);
vm.runInContext(appSource, context);

// The theme toggle must be labelled from the stored theme before any data loads.
if (element("theme-toggle-label").textContent !== "Dark" || element("theme-toggle").getAttribute("aria-label") !== "Switch to light theme") {
  throw new Error("theme toggle should be synchronised with the stored theme at startup");
}

vm.runInContext(`
  window.location.hash = "#overview";
  applyRoute({ view: "overview" });
  updateRouteHash({ view: "companies", query: "nvidia" }, true);
  globalThis.__hashAfterFilter = window.location.hash;
  window.location.hash = "#overview";
  globalThis.__routeBeforeBack = currentRoute.view;
  handleLocationChange();
  globalThis.__routeAfterBack = currentRoute.view;
`, context);

if (context.__hashAfterFilter !== "#companies?query=nvidia" || context.__routeBeforeBack !== "companies" || context.__routeAfterBack !== "overview") {
  throw new Error(`Back after a filter route must re-apply the previous route: ${context.__hashAfterFilter} ${context.__routeBeforeBack} -> ${context.__routeAfterBack}`);
}

const roundTrip = context.hashToRoute(context.routeToHash({
  view: "companies",
  query: "tsm",
  sector: "Technology",
  connected: "1",
}));

if (roundTrip.view !== "companies" || roundTrip.query !== "tsm" || roundTrip.sector !== "Technology" || roundTrip.connected !== "1") {
  throw new Error(`route round-trip failed: ${JSON.stringify(roundTrip)}`);
}

if (context.routeToHash({ view: "company", ticker: "AMD", previous: { view: "companies", query: "amd" } }) !== "#company?ticker=AMD") {
  throw new Error("route hash should not leak nested previous route state");
}

vm.runInContext(`
  allCompanies = [
    {
      name: "Advanced Micro Devices, Inc. Common Stock",
      ticker: "AMD",
      sector: "Technology",
      industry: "Semiconductors",
      connection_count: 1,
      upstream: [{ ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", confidence: 0.95, review_status: "approved", source_type: "AI Research", last_verified: "2026-06-01" }],
      downstream: [],
    },
    {
      name: "Acme Retail",
      ticker: "ACME",
      sector: "Consumer",
      industry: "Retail",
      connection_count: 1,
      upstream: [{ ticker: "AMD", name: "Advanced Micro Devices", type: "Processors", confidence: 0.6, review_status: "pending" }],
      downstream: [],
    },
    {
      name: "Applied Materials",
      ticker: "AMAT",
      sector: "Technology",
      industry: "Semiconductor Equipment",
      connection_count: 0,
      upstream: [],
      downstream: [],
    },
    {
      name: "International Business Machines",
      ticker: "IBM",
      sector: "Technology",
      industry: "Information Technology Services",
      connection_count: 0,
      upstream: [],
      downstream: [],
    },
  ];
  globalData = {
    Technology: [allCompanies[0], allCompanies[2], allCompanies[3]],
    Consumer: [allCompanies[1]],
    // Synthetic repair bucket with more link rows than any real sector; it must never
    // be reported as the top sector.
    "Linked Companies": [{
      name: "Novartis",
      ticker: "NVS",
      sector: "Linked Companies",
      industry: "Reviewed relationship endpoint",
      price: null,
      change: 0,
      connection_count: 3,
      upstream: [
        { ticker: "A", name: "A", type: "X", relationship_key: "A->NVS:X" },
        { ticker: "B", name: "B", type: "Y", relationship_key: "B->NVS:Y" },
        { ticker: "C", name: "C", type: "Z", relationship_key: "C->NVS:Z" },
      ],
      downstream: [],
    }],
  };
  dashboardMeta = { investor_metrics: { unique_links: 12 } };
`, context);

vm.runInContext("globalThis.__caseInsensitiveTicker = getCompanyByTicker('amd')?.ticker;", context);

if (context.__caseInsensitiveTicker !== "AMD") {
  throw new Error("ticker lookup should be case-insensitive");
}

vm.runInContext("globalThis.__cleanDisplayName = displayCompanyName('Advanced Micro Devices, Inc. Common Stock');", context);

if (context.__cleanDisplayName !== "Advanced Micro Devices, Inc.") {
  throw new Error(`display name should remove security suffixes, got ${context.__cleanDisplayName}`);
}

vm.runInContext("globalThis.__sentenceName = sentenceEntityName('Super Micro Computer, Inc. Common Stock'); globalThis.__formattedSmallCurrency = formatNum(527.2);", context);

if (context.__sentenceName !== "Super Micro Computer, Inc") {
  throw new Error(`sentence entity name should avoid double periods, got ${context.__sentenceName}`);
}

if (context.__formattedSmallCurrency !== "$527.20") {
  throw new Error(`small currency values should keep cents, got ${context.__formattedSmallCurrency}`);
}

vm.runInContext(`
  globalThis.__formattedNegative = formatNum(-4355558);
  globalThis.__missingPrice = formatPrice({ price: null, change: 0 });
  globalThis.__missingChange = formatChange({ price: null, change: 0 });
  globalThis.__presentChange = formatChange({ price: 10, change: 1.5 });
`, context);

if (context.__formattedNegative !== "-$4.36M") {
  throw new Error(`negative currency values should use the compact format, got ${context.__formattedNegative}`);
}

if (context.__missingPrice !== "N/A" || context.__missingChange !== "N/A" || context.__presentChange !== "+1.50%") {
  throw new Error(`missing prices must render as N/A, not $0.00: ${context.__missingPrice} ${context.__missingChange} ${context.__presentChange}`);
}

vm.runInContext(`
  globalThis.__distinctLinks = distinctLinkCount([
    { upstream: [{ relationship_key: "TSM->AMD:FOUNDRY" }], downstream: [] },
    { upstream: [], downstream: [{ relationship_key: "TSM->AMD:FOUNDRY" }, { relationship_key: "ASML->TSM:EQUIPMENT" }] },
  ]);
`, context);

if (context.__distinctLinks !== 2) {
  throw new Error(`sector link counts must not double-count relationships published from both endpoints, got ${context.__distinctLinks}`);
}

vm.runInContext("renderOverviewStats();", context);
const overviewStatsText = collectText(element("overview-stats"));
["4Companies", "12Supply links", "2Linked names", "TechnologyTop sector"].forEach((expected) => {
  if (!overviewStatsText.includes(expected)) {
    throw new Error(`overview stats missing ${expected}; got ${overviewStatsText}`);
  }
});

vm.runInContext(`
  openEvidenceModal({ ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", product: "wafers", confidence: 0.95, source_type: "AI Research", last_verified: "2026-06-01" }, { ticker: "AMD" }, "upstream");
`, context);
const modalText = collectText(element("modal-body"));
["95%", "AI Research", "2026-06-01", "No evidence excerpt was saved for this relationship."].forEach((expected) => {
  if (!modalText.includes(expected)) {
    throw new Error(`details modal missing ${expected}; got ${modalText}`);
  }
});

element("search-input").value = "taiwan";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = true;

vm.runInContext("applyFilters(false); globalThis.__filteredTickers = currentCompaniesList.map(company => company.ticker);", context);

if (context.__filteredTickers.length !== 1 || context.__filteredTickers[0] !== "AMD") {
  throw new Error(`expected richer search/filter to match AMD only, got ${JSON.stringify(context.__filteredTickers)}`);
}

vm.runInContext("currentRoute = { view: 'overview' }; currentCompaniesList = [];", context);
element("search-input").value = "a";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = false;

vm.runInContext("applyFilters(true); globalThis.__routeAfterPartialTicker = currentRoute; globalThis.__partialResults = currentCompaniesList.map(company => company.ticker);", context);

if (context.__routeAfterPartialTicker.view !== "overview" || context.__partialResults.length !== 0) {
  throw new Error(`expected partial ticker typing to stay on overview, got route ${JSON.stringify(context.__routeAfterPartialTicker)} and results ${JSON.stringify(context.__partialResults)}`);
}

element("search-input").value = "am";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = false;

vm.runInContext("applyFilters(true); globalThis.__routeAfterTickerPrefixTyping = currentRoute; globalThis.__prefixTypingResults = currentCompaniesList.map(company => company.ticker).sort();", context);

if (context.__routeAfterTickerPrefixTyping.view !== "overview" || context.__prefixTypingResults.length !== 0) {
  throw new Error(`expected two-letter ticker typing to stay on overview, got ${JSON.stringify(context.__routeAfterTickerPrefixTyping)} and results ${JSON.stringify(context.__prefixTypingResults)}`);
}

if (context.routeToHash({ view: "predictions" }) !== "#predictions") {
  throw new Error("prediction route should be shareable without extra query state");
}

vm.runInContext("currentRoute = { view: 'companies', query: 'amd' }; currentCompaniesList = [allCompanies[0], allCompanies[2]];", context);
element("search-input").value = "am";
vm.runInContext("applyFilters(true); globalThis.__routeAfterStalePrefixTyping = currentRoute; globalThis.__stalePrefixResults = currentCompaniesList.map(company => company.ticker).sort();", context);

if (context.__routeAfterStalePrefixTyping.view !== "overview" || context.__stalePrefixResults.length !== 0) {
  throw new Error(`expected two-letter ticker typing to clear stale results, got ${JSON.stringify(context.__routeAfterStalePrefixTyping)} and results ${JSON.stringify(context.__stalePrefixResults)}`);
}

element("search-input").value = "am";
vm.runInContext("handleSearchKeydown({ key: 'Enter' }); globalThis.__routeAfterTickerPrefix = currentRoute; globalThis.__prefixResults = currentCompaniesList.map(company => company.ticker).sort();", context);

if (context.__routeAfterTickerPrefix.view !== "companies" || context.__routeAfterTickerPrefix.query !== "am") {
  throw new Error(`expected committed ticker prefix search to route to companies, got ${JSON.stringify(context.__routeAfterTickerPrefix)}`);
}

if (JSON.stringify(context.__prefixResults) !== JSON.stringify(["AMAT", "AMD"])) {
  throw new Error(`expected committed AM ticker prefix matches only, got ${JSON.stringify(context.__prefixResults)}`);
}

vm.runInContext("currentRoute = { view: 'overview' }; currentCompaniesList = [];", context);
element("search-input").value = "a";
vm.runInContext("handleSearchKeydown({ key: 'Enter' }); globalThis.__routeAfterOneLetterEnter = currentRoute; globalThis.__oneLetterResults = currentCompaniesList.map(company => company.ticker);", context);

if (context.__routeAfterOneLetterEnter.view !== "overview" || context.__oneLetterResults.length !== 0) {
  throw new Error(`expected one-letter committed search to stay on overview unless exact, got ${JSON.stringify(context.__routeAfterOneLetterEnter)} and results ${JSON.stringify(context.__oneLetterResults)}`);
}

element("search-input").value = "am";
vm.runInContext("applyRoute({ view: 'companies', query: 'am' }); globalThis.__routeAfterCommittedPrefixRoute = currentRoute; globalThis.__committedPrefixRouteResults = currentCompaniesList.map(company => company.ticker).sort();", context);

if (context.__routeAfterCommittedPrefixRoute.view !== "companies" || JSON.stringify(context.__committedPrefixRouteResults) !== JSON.stringify(["AMAT", "AMD"])) {
  throw new Error(`expected restored AM route to behave as a committed prefix search, got ${JSON.stringify(context.__routeAfterCommittedPrefixRoute)} and results ${JSON.stringify(context.__committedPrefixRouteResults)}`);
}

element("search-input").value = "zzzznotaticker";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = false;

vm.runInContext("applyFilters(true); globalThis.__emptySearchResults = currentCompaniesList.map(company => company.ticker); globalThis.__emptySearchCount = document.getElementById('result-count').textContent;", context);

if (context.__emptySearchResults.length !== 0 || context.__emptySearchCount !== "0 results") {
  throw new Error(`expected impossible search to stay empty, got ${JSON.stringify(context.__emptySearchResults)} and count ${context.__emptySearchCount}`);
}

element("search-input").value = "amd";
element("sector-filter").value = "";
element("dependency-filter").value = "";
element("connected-filter").checked = false;

vm.runInContext("applyFilters(true); globalThis.__routeAfterTickerSearch = currentRoute; globalThis.__exactTickerResults = currentCompaniesList.map(company => company.ticker);", context);

if (context.__routeAfterTickerSearch.view !== "companies" || context.__routeAfterTickerSearch.query !== "amd") {
  throw new Error(`expected exact ticker typing to stay in list search, got ${JSON.stringify(context.__routeAfterTickerSearch)}`);
}

if (JSON.stringify(context.__exactTickerResults) !== JSON.stringify(["AMD"])) {
  throw new Error(`expected exact ticker typing to filter to AMD only, got ${JSON.stringify(context.__exactTickerResults)}`);
}

vm.runInContext("handleSearchKeydown({ key: 'Enter' }); globalThis.__routeAfterTickerEnter = currentRoute;", context);

if (context.__routeAfterTickerEnter.view !== "company" || context.__routeAfterTickerEnter.ticker !== "AMD") {
  throw new Error(`expected Enter on exact ticker to open AMD detail, got ${JSON.stringify(context.__routeAfterTickerEnter)}`);
}

element("search-input").value = "taiwan";
vm.runInContext("applyFilters(true); globalThis.__routeAfterListSearch = currentRoute;", context);

if (context.__routeAfterListSearch.view !== "companies" || context.__routeAfterListSearch.query !== "taiwan") {
  throw new Error(`expected list search to update route without rerouting, got ${JSON.stringify(context.__routeAfterListSearch)}`);
}

vm.runInContext(`
  currentRoute = { view: "sector", sector: "Technology" };
  navigateCompany("AMD");
  globalThis.__sectorBackLabel = document.getElementById('detail-back-button').textContent;
  navigateBackToCompanies();
  globalThis.__routeAfterSectorBack = currentRoute;
`, context);

if (context.__routeAfterSectorBack.view !== "sector" || context.__routeAfterSectorBack.sector !== "Technology") {
  throw new Error(`expected detail back button to restore sector route, got ${JSON.stringify(context.__routeAfterSectorBack)}`);
}

if (context.__sectorBackLabel !== "Back to sector") {
  throw new Error(`expected sector detail back button label to match destination, got ${context.__sectorBackLabel}`);
}

element("compare-a").value = "AMD";
element("compare-b").value = "IBM";
vm.runInContext(`
  renderCompareView(false);
  navigateCompany("AMD");
  globalThis.__compareBackLabel = document.getElementById('detail-back-button').textContent;
  navigateBackToCompanies();
  globalThis.__routeAfterCompareBack = currentRoute;
`, context);

if (context.__routeAfterCompareBack.view !== "compare" || context.__routeAfterCompareBack.a !== "AMD" || context.__routeAfterCompareBack.b !== "IBM") {
  throw new Error(`expected detail back button to restore compare route, got ${JSON.stringify(context.__routeAfterCompareBack)}`);
}

if (context.__compareBackLabel !== "Back to compare") {
  throw new Error(`expected compare detail back button label to match destination, got ${context.__compareBackLabel}`);
}
