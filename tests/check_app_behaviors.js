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

// The search box and the filters used to sit inside #view-industries, so every debounced
// re-render hid the input mid-word and put "Reset filters" out of reach.
const industriesBlock = htmlSource.slice(
  htmlSource.indexOf('<section id="view-industries">'),
  htmlSource.indexOf('<section id="view-sector"'),
);
if (industriesBlock.includes('id="search-input"') || industriesBlock.includes('class="control-panel"')) {
  throw new Error("search and filters must live outside the view they re-render");
}

if (!htmlSource.includes('id="search-panel"') || !appSource.includes("'view-hero', 'search-panel', 'view-industries'")) {
  throw new Error("the search panel must be shown and hidden independently of the overview view");
}

if (htmlSource.includes("<th onclick=") || !htmlSource.includes('data-sort="name"') || !htmlSource.includes('class="sort-button"')) {
  throw new Error("sortable column headers must be real buttons carrying their sort key");
}

if (!htmlSource.includes('role="dialog"') || !htmlSource.includes('aria-modal="true"') || !appSource.includes("function handleModalKeydown")) {
  throw new Error("the evidence modal must be a dialog that can be closed and escaped from the keyboard");
}

if (!htmlSource.includes('<nav class="topbar-actions"') || !appSource.includes("aria-current")) {
  throw new Error("primary navigation must be a landmark that marks the current page");
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
    classes: new Set(),
    dataset: {},
    href: "",
    disabled: false,
    classList: {
      add(...names) { names.forEach((name) => node.classes.add(name)); },
      remove(...names) { names.forEach((name) => node.classes.delete(name)); },
      toggle(name, force) {
        const on = force === undefined ? !node.classes.has(name) : Boolean(force);
        if (on) node.classes.add(name);
        else node.classes.delete(name);
        return on;
      },
      contains(name) { return node.classes.has(name); },
    },
    focus() { context.document.activeElement = node; },
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
    removeAttribute(name) { delete node.attributes[name]; },
    querySelector() { return null; },
  };
  Object.defineProperty(node, "firstChild", {
    get() {
      return node.children[0] || null;
    },
  });
  return node;
}

const documentHandlers = {};
const windowHandlers = {};
const dispatchedEvents = [];
const selectorNodes = {};
const storageValues = new Map();

function dispatchKeydown(init) {
  const event = Object.assign({ shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false }, init);
  event.preventDefault = () => { event.defaultPrevented = true; };
  (documentHandlers.keydown || []).forEach((handler) => handler(event));
  return event;
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
  URL,
  URLSearchParams,
  setTimeout,
  clearTimeout,
  CustomEvent: class { constructor(type) { this.type = type; } },
  localStorage: {
    getItem: (key) => (storageValues.has(key) ? storageValues.get(key) : null),
    setItem: (key, value) => { storageValues.set(key, String(value)); },
  },
  fetch: () => new Promise(() => {}),
  document: {
    createElement: (tag) => mockElement(tag),
    getElementById: element,
    querySelectorAll: (selector) => selectorNodes[selector] || [],
    addEventListener(type, handler) {
      (documentHandlers[type] = documentHandlers[type] || []).push(handler);
    },
    activeElement: null,
  },
  history: {
    pushed: [],
    replaced: [],
    pushState(_state, _title, hash) { this.pushed.push(hash); context.window.location.hash = hash; },
    replaceState(_state, _title, hash) { this.replaced.push(hash); context.window.location.hash = hash; },
  },
  window: {
    location: { hash: "" },
    addEventListener(type, handler) { (windowHandlers[type] = windowHandlers[type] || []).push(handler); },
    dispatchEvent(event) { dispatchedEvents.push(event.type); return true; },
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

const quietChangesText = collectText(element("overview-changes"));
if (!quietChangesText.includes("Change tracking starts")) {
  throw new Error(`overview should explain missing change data; got ${quietChangesText}`);
}

vm.runInContext(`
  dashboardMeta = { investor_metrics: { unique_links: 12, change_summary: {
    previous_generated_on: "2026-09-01", current_generated_on: "2026-09-02",
    previous_unique_links: 10, current_unique_links: 12, net_change: 2, new_count: 2, removed_count: 1, changed_count: 0,
    new_links: [
      { relationship_key: "TSM->AMD:FOUNDRY", source_ticker: "TSM", target_ticker: "AMD", type: "Foundry", product: "wafers" },
      { relationship_key: "ASML->TSM:EQUIPMENT", source_ticker: "ASML", target_ticker: "TSM", type: "Equipment", product: "Equipment" },
    ],
    removed_links: [{ relationship_key: "X->Y:Z", source_ticker: "X", target_ticker: "Y", type: "Z", product: "Z" }],
    changed_links: [],
  }, history: [
    { generated_on: "2026-09-01", unique_links: 10 },
    { generated_on: "2026-09-02", unique_links: 12 },
  ] } };
  renderOverviewStats();
`, context);
const changesText = collectText(element("overview-changes"));
["+2 net supply links", "since Sep 1, 2026", "2 new, 1 removed, 0 updated", "New (2)", "Removed (1)", "TSM", "AMD", "Foundry · wafers", "Sep 1, 2026 → Sep 2, 2026: 10 → 12 links", "RSS", "JSON"].forEach((expected) => {
  if (!changesText.includes(expected)) {
    throw new Error(`change panel missing ${expected}; got ${changesText}`);
  }
});
vm.runInContext("dashboardMeta = { investor_metrics: { unique_links: 12 } };", context);

vm.runInContext(`
  openEvidenceModal({ ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", product: "wafers", confidence: 0.95, source_type: "AI Research", last_verified: "2026-06-01" }, { ticker: "AMD" }, "upstream");
`, context);
const modalText = collectText(element("modal-body"));
["95%", "AI Research", "Jun 1, 2026", "No evidence excerpt was saved for this relationship.", "AI research · no direct citation", "Awaiting review", "How links are verified"].forEach((expected) => {
  if (!modalText.includes(expected)) {
    throw new Error(`details modal missing ${expected}; got ${modalText}`);
  }
});

vm.runInContext(`
  openEvidenceModal({
    ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", product: "wafers", confidence: 0.9, review_status: "approved",
    source: "https://www.sec.gov/Archives/edgar/data/1/10k.htm", source_title: "SEC EDGAR (10-K filed 2025-10-31)", source_type: "Web Source",
    review_summary: { method: "consensus", label: "Consensus panel 3/3 models", votes_for: 3, votes_total: 3, rationale: "TSMC fabricates AMD's processors." },
    evidence_excerpt: "TSMC fabricates the processors AMD sells.", last_verified: "2026-09-01",
  }, { ticker: "AMD" }, "upstream");
`, context);
const citedModalText = collectText(element("modal-body"));
["SEC EDGAR (10-K filed 2025-10-31)", "Consensus panel 3/3 models", "Reviewer rationale: TSMC fabricates AMD's processors.", "Open source"].forEach((expected) => {
  if (!citedModalText.includes(expected)) {
    throw new Error(`evidence modal missing provenance ${expected}; got ${citedModalText}`);
  }
});

vm.runInContext(`
  globalThis.__provenance = [
    provenanceLabel({ source: "AI Multi-Source Research", source_type: "AI Research" }),
    provenanceLabel({ source: "Manual System Jumpstart", source_type: "Manual" }),
    provenanceLabel({ source: "https://www.sec.gov/x", source_title: "SEC EDGAR (10-K)" }),
    reviewLabel({ review_status: "approved" }),
    reviewLabel({ review_status: "pending" }),
  ];
`, context);
if (JSON.stringify(context.__provenance) !== JSON.stringify(["AI research · no direct citation", "Curated seed", "SEC EDGAR (10-K)", "Reviewed", "Awaiting review"])) {
  throw new Error(`provenance labels wrong: ${JSON.stringify(context.__provenance)}`);
}

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

// A one-letter query that is an exact ticker is a result, not a "keep typing" state:
// 17 such tickers exist (A, F, T, ...), and #companies?query=T rendered the overview.
vm.runInContext([
  "const __savedCompanies = allCompanies.slice();",
  "allCompanies.push({ ticker: 'F', name: 'Ford Motor Company', sector: 'Consumer Cyclical', upstream: [], downstream: [], connection_count: 0 });",
  "document.getElementById('search-input').value = 'F';",
  "currentRoute = { view: 'companies' };",
  "applyFilters(false, true);",
  "globalThis.__singleLetterView = currentRoute.view;",
  "globalThis.__singleLetterResults = currentCompaniesList.map(company => company.ticker).join(',');",
  "allCompanies = __savedCompanies;",
  "document.getElementById('search-input').value = '';",
].join("\n"), context);
if (context.__singleLetterView !== "companies" || String(context.__singleLetterResults) !== "F") {
  throw new Error(`a one-letter exact ticker must render that company, not the overview: view=${context.__singleLetterView} results=${context.__singleLetterResults}`);
}

// A search typed just before navigating must not re-render the screener over the
// destination view once its debounce fires.
const clearedTimers = [];
const realClearTimeout = context.window.clearTimeout;
context.window.clearTimeout = (id) => { clearedTimers.push(id); return realClearTimeout(id); };
vm.runInContext("document.getElementById('search-input').value = 'nvid'; handleSearchInput(); globalThis.__armedTimer = searchInputTimer; applyRoute({ view: 'overview' });", context);
context.window.clearTimeout = realClearTimeout;
if (!context.__armedTimer) {
  throw new Error("typing in the search box must arm the debounce timer");
}
if (!clearedTimers.includes(context.__armedTimer)) {
  throw new Error("navigating must clear the pending search debounce, or it re-renders the screener over the new view");
}

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

vm.runInContext(`
  const __down = exposureFrom("TSM", "downstream");
  const __up = exposureFrom("ACME", "upstream");
  globalThis.__exposureDown = __down.map(entry => [entry.ticker, entry.hop, entry.path.join(">")]);
  globalThis.__exposureUp = __up.map(entry => [entry.ticker, entry.hop, entry.path.join(">")]);
  globalThis.__singleSource = isSingleSource({ evidence_excerpt: "TSMC is the sole source of our advanced wafers." });
  globalThis.__notSingleSource = isSingleSource({ evidence_excerpt: "TSMC supplies some wafers." });
`, context);
if (JSON.stringify(context.__exposureDown) !== JSON.stringify([["AMD", 1, "TSM>AMD"], ["ACME", 2, "TSM>AMD>ACME"]])) {
  throw new Error(`downstream exposure should walk two hops: ${JSON.stringify(context.__exposureDown)}`);
}
if (JSON.stringify(context.__exposureUp) !== JSON.stringify([["AMD", 1, "AMD>ACME"], ["TSM", 2, "TSM>AMD>ACME"]])) {
  throw new Error(`upstream exposure should walk two hops: ${JSON.stringify(context.__exposureUp)}`);
}
if (context.__singleSource !== true || context.__notSingleSource !== false) {
  throw new Error("single-source detection should key off evidence language");
}

element("exposure-input").value = "TSM";
vm.runInContext("renderExposureView(false); globalThis.__exposureRoute = currentRoute;", context);
const exposureText = [collectText(element("exposure-summary")), collectText(element("exposure-results")), element("exposure-count").textContent, element("exposure-title").textContent].join(" | ");
["1Direct dependents", "1Second-order", "Exposed to TSM (2)", "What TSM depends on (0)", "2 exposed · 0 upstream", "Who is exposed to TSM"].forEach((expected) => {
  if (!exposureText.includes(expected)) {
    throw new Error(`exposure view missing ${expected}; got ${exposureText}`);
  }
});
if (context.__exposureRoute.view !== "exposure" || context.__exposureRoute.ticker !== "TSM") {
  throw new Error(`exposure route should carry the ticker: ${JSON.stringify(context.__exposureRoute)}`);
}

vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 250, hit_rate: 0.476 }, track_record: {
    status: "established", minimum_resolved: 30, resolved: 250, hits: 119, hit_rate: 0.476, always_up_hit_rate: 0.584,
    matured_unresolved: 3, by_direction: { up: { resolved: 196, hit_rate: 0.5 }, neutral: { resolved: 54, hit_rate: 0.39 } }, latest_evaluated_on: "2026-09-02",
  } };
  renderPredictionsView();
  globalThis.__trackRecordClass = document.getElementById('prediction-track-record').className;
`, context);
const trackRecordText = collectText(element("prediction-track-record"));
["48% of 250 resolved 30‑day signals", "would have scored 58%", "not beaten a naive baseline", "up: 50% of 196", "3 matured, awaiting price data", "Last scored Sep 2, 2026"].forEach((expected) => {
  if (!trackRecordText.includes(expected)) {
    throw new Error(`track record banner missing ${expected}; got ${trackRecordText}`);
  }
});
if (!String(context.__trackRecordClass).includes("underperforming")) {
  throw new Error(`track record should be flagged as underperforming, got ${context.__trackRecordClass}`);
}

vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 4, hit_rate: 0.5 } };
  renderPredictionsView();
`, context);
const experimentalText = collectText(element("prediction-track-record"));
if (!experimentalText.includes("Experimental: 4 of 30 signals resolved")) {
  throw new Error(`experimental banner missing; got ${experimentalText}`);
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

// Counts agree with their noun, and published dates render in UTC with one format
// (a run finishing at 00:16 UTC showed the previous day to visitors in the Americas).
vm.runInContext("globalThis.__plurals = [pluralize(1, 'link'), pluralize(2, 'link'), pluralize(1, 'company', 'companies'), pluralize(0, 'result')].join('|'); globalThis.__utcDate = formatDisplayDate('2026-09-12T00:16:12+00:00'); globalThis.__dateOnly = formatDisplayDate('2026-09-11');", context);
if (context.__plurals !== "1 link|2 links|1 company|0 results") {
  throw new Error(`counts must agree with their noun: ${context.__plurals}`);
}
if (context.__utcDate !== "Sep 12, 2026" || context.__dateOnly !== "Sep 11, 2026") {
  throw new Error(`published dates must render in UTC with one format: ${context.__utcDate} / ${context.__dateOnly}`);
}


// --- Search and filters survive the view they drive ---------------------------------
// Typing "NVDA" used to leave "NVD" in the box: the debounced render hid #view-industries,
// which contained the input, so focus dropped to <body> and the last keystroke was lost.
vm.runInContext(`
  renderLevel1();
  globalThis.__panelOnOverview = document.getElementById('search-panel').classList.contains('hidden');
  document.getElementById('search-input').value = 'taiwan';
  document.getElementById('search-input').focus();
  applyFilters(false);
  globalThis.__panelOnScreener = document.getElementById('search-panel').classList.contains('hidden');
  globalThis.__heroOnScreener = document.getElementById('view-hero').classList.contains('hidden');
  globalThis.__focusAfterFilter = document.activeElement && document.activeElement.id;
`, context);

if (context.__panelOnOverview || context.__panelOnScreener) {
  throw new Error("search and filters must stay on screen on both the overview and the screener");
}
if (!context.__heroOnScreener) {
  throw new Error("the overview hero should still be hidden once results are shown");
}
if (context.__focusAfterFilter !== "search-input") {
  throw new Error(`filtering must not move focus out of the search box, got ${context.__focusAfterFilter}`);
}

// '/' focuses the search box wherever the panel is shown, and does nothing where it is not.
context.document.activeElement = null;
dispatchKeydown({ key: "/" });
if (!context.document.activeElement || context.document.activeElement.id !== "search-input") {
  throw new Error("'/' should focus the search box while the search panel is visible");
}

vm.runInContext("hideAllViews();", context);
context.document.activeElement = null;
dispatchKeydown({ key: "/" });
if (context.document.activeElement) {
  throw new Error("'/' must not focus a hidden search box");
}

// --- The evidence modal is a dialog: Escape closes it and focus comes back -----------
const modalOpener = mockElement("tr", "opening-row");
context.document.activeElement = modalOpener;
vm.runInContext(`
  openEvidenceModal({ ticker: "TSM", name: "Taiwan Semiconductor", type: "Foundry", product: "wafers", confidence: 0.9 }, { ticker: "AMD" }, "upstream");
  globalThis.__modalHiddenAfterOpen = document.getElementById('evidence-modal').classList.contains('hidden');
  globalThis.__focusAfterOpen = document.activeElement && document.activeElement.id;
`, context);

if (context.__modalHiddenAfterOpen) {
  throw new Error("opening the evidence modal should show it");
}
if (context.__focusAfterOpen !== "evidence-dialog") {
  throw new Error(`opening the modal should move focus into the dialog, got ${context.__focusAfterOpen}`);
}

const escapeEvent = dispatchKeydown({ key: "Escape" });
if (!element("evidence-modal").classList.contains("hidden")) {
  throw new Error("Escape should close the evidence modal");
}
if (context.document.activeElement !== modalOpener) {
  throw new Error("closing the modal should return focus to whatever opened it");
}
if (!escapeEvent) {
  throw new Error("Escape handling should run through the document keydown listener");
}

// --- Sorted column announced, not only drawn ----------------------------------------
selectorNodes["th[data-sort]"] = ["name", "ticker", "price"].map((column) => {
  const header = mockElement("th");
  header.setAttribute("data-sort", column);
  return header;
});
vm.runInContext("sortCol = 'name'; sortAsc = true; renderLevel2();", context);
const [nameHeader, tickerHeader] = selectorNodes["th[data-sort]"];
if (nameHeader.getAttribute("aria-sort") !== "ascending" || tickerHeader.getAttribute("aria-sort") !== "none") {
  throw new Error(`aria-sort must track the sorted column, got ${nameHeader.getAttribute("aria-sort")} / ${tickerHeader.getAttribute("aria-sort")}`);
}

// --- Reset clears the helper line with the box --------------------------------------
vm.runInContext(`
  document.getElementById('search-input').value = 'taiwan';
  applyFilters(false);
  resetDashboard();
  globalThis.__helperAfterReset = document.getElementById('search-helper').textContent;
`, context);
if (context.__helperAfterReset !== "Search ticker, company (private companies included), supplier, customer, product, or sector.") {
  throw new Error(`resetting should clear the search helper, got ${context.__helperAfterReset}`);
}

// --- A product that already states the share gets no duplicate badge ----------------
vm.runInContext(`
  globalThis.__restates = [
    restatesShare({ product: '81% of ROKU revenue' }, '81% of revenue'),
    restatesShare({ product: '10.5% of HLIT revenue' }, '10.5% of revenue'),
    restatesShare({ product: 'Set-top boxes' }, '81% of revenue'),
    restatesShare({ product: '116% of revenue' }, '16% of revenue'),
  ];
  globalThis.__shardKeys = [detailShardKey('AMD'), detailShardKey('\u00c6ON'), detailShardKey('$X'), detailShardKey('')].join('|');
`, context);
if (JSON.stringify(context.__restates) !== JSON.stringify([true, true, false, false])) {
  throw new Error(`share badges must not repeat a share the product already states: ${JSON.stringify(context.__restates)}`);
}
if (context.__shardKeys !== "A|Æ|_|_") {
  throw new Error(`detail shard keys must match the keys split_dashboard.py writes: ${context.__shardKeys}`);
}

// --- An unscored direction is not a 0% hit rate -------------------------------------
vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 40, hit_rate: 0.5 }, track_record: {
    status: "established", minimum_resolved: 30, resolved: 40, hit_rate: 0.5, always_up_hit_rate: 0.4,
    matured_unresolved: 0, by_direction: { down: { resolved: 6, hit_rate: null } },
  } };
  renderPredictionsView();
`, context);
const unscoredText = collectText(element("prediction-track-record"));
if (!unscoredText.includes("down: 6 resolved, not scored yet") || unscoredText.includes("down: 0%")) {
  throw new Error(`a direction with no hit rate must not render as 0%; got ${unscoredText}`);
}

// --- Track record counts independent windows and leaves neutral calls unscored -------
vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 650, hit_rate: 0.45 }, track_record: {
    status: "experimental", minimum_resolved: 30, minimum_periods: 3, independent_periods: 1, resolved: 142,
    resolved_with_overlap: 650, no_calls: 166, hits: 70, hit_rate: 0.493, always_up_hit_rate: 0.542,
    matured_unresolved: 0, by_direction: { up: { resolved: 142, hit_rate: 0.493 } }, latest_evaluated_on: "2026-09-28",
  } };
  renderPredictionsView();
`, context);
const independentText = collectText(element("prediction-track-record"));
["Experimental: 1 independent 30‑day period of 3 scored so far", "650 daily signals, 142 counted once per company per window", "166 neutral signals not scored (no call)"].forEach((expected) => {
  if (!independentText.includes(expected)) {
    throw new Error(`track record must explain overlap and abstentions; missing ${expected}; got ${independentText}`);
  }
});

function check(condition, message) {
  if (!condition) throw new Error(message);
}

// --- The strip under the banner agrees with the banner ---------------------------------
// It used calibration.hit_rate / resolved_predictions (554 overlapping daily signals, 53%)
// directly under a banner counting 165 independent signals at 54%.
vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 554, hit_rate: 0.527 }, track_record: {
    status: "experimental", minimum_resolved: 30, minimum_periods: 3, independent_periods: 2, resolved: 165, resolved_with_overlap: 554,
    hits: 89, hit_rate: 0.539, always_up_hit_rate: 0.539, matured_unresolved: 0,
    by_direction: { up: { resolved: 165, hit_rate: 0.539 } }, latest_evaluated_on: "2026-10-05",
  } };
  renderPredictionsView();
`, context);
const stripText = collectText(element("prediction-calibration"));
["Experimental: 54% hit rate so far", "165 resolved signals, 2 of 3 independent periods", 'Always saying "up" scores the same: every scored call so far was "up"'].forEach((expected) => {
  check(stripText.includes(expected), `prediction strip missing ${expected}; got ${stripText}`);
});
check(!stripText.includes("554") && !stripText.includes("53%"), `the strip must not show the overlapping calibration numbers: ${stripText}`);

vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 250, hit_rate: 0.9 }, track_record: {
    status: "established", minimum_resolved: 30, resolved: 250, hit_rate: 0.476, always_up_hit_rate: 0.584,
    by_direction: { up: { resolved: 196, hit_rate: 0.5 }, neutral: { resolved: 0, hit_rate: null } },
  } };
  renderPredictionsView();
`, context);
const establishedStrip = collectText(element("prediction-calibration"));
check(establishedStrip.includes("48% hit rate") && !establishedStrip.includes("Experimental") && establishedStrip.includes("250 resolved signals"), `established strip wrong: ${establishedStrip}`);
check(establishedStrip.includes('Always saying "up" scores 58%') && !establishedStrip.includes("every scored call"), `a different baseline is stated as a number: ${establishedStrip}`);

vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 4, hit_rate: 0.5 }, track_record: { status: "experimental", resolved: 0, hit_rate: null, by_direction: {} } };
  renderPredictionsView();
`, context);
const calibratingStrip = collectText(element("prediction-calibration"));
check(calibratingStrip.includes("Calibrating") && !calibratingStrip.includes("%") && !calibratingStrip.includes("Always saying"), `no hit rate means Calibrating: ${calibratingStrip}`);

vm.runInContext(`
  predictionData = { predictions: [], calibration: { resolved_predictions: 4, hit_rate: 0.5 } };
  renderPredictionsView();
`, context);
const legacyStrip = collectText(element("prediction-calibration"));
check(legacyStrip.includes("Experimental: 50% hit rate so far") && legacyStrip.includes("4 resolved signals"), `a payload without track_record falls back to calibration: ${legacyStrip}`);
vm.runInContext("predictionData = { predictions: [], calibration: {} };", context);

// --- The two weak scores are gone from the interface ------------------------------------------
// "Larger-side share" (most companies read 100%) and "Data gap" (most read within a few points of
// each other) did not say what a reader took them to say, so the brief shows counts only.
["Larger-side share", "Data gap", "Concentration", "Risk", "brief-concentration", "brief-verified", "methodology.html#scores"].forEach((label) => {
  check(!htmlSource.includes(label), `the brief must not carry ${label}`);
});
["Larger-side share", "Data gap", "formatLargerSide", "formatDataGap", "SCORE_HELP", "brief-concentration", "brief-verified", "largest data gap first"].forEach((label) => {
  check(!appSource.includes(label), `app.js must not carry ${label}`);
});
check(htmlSource.includes("<small>Suppliers</small>") && htmlSource.includes("<small>Customers</small>"), "the brief shows supplier and customer counts");

vm.runInContext(`
  renderDecisionBrief({ ticker: "AMD", name: "AMD", upstream: [{ ticker: "TSM" }], downstream: [],
    investor_metrics: { total_links: 1, upstream_count: 1, downstream_count: 0, concentration_score: 1, risk_score: 46 } });
`, context);
check(element("brief-confidence").textContent === "1" && element("brief-approved").textContent === "0", "a company with links shows its supplier and customer counts");
vm.runInContext(`renderDecisionBrief({ ticker: "AMAT", name: "Applied Materials", upstream: [], downstream: [] });`, context);
check(element("brief-confidence").textContent === "0" && element("brief-approved").textContent === "0", "no links is 0 and 0");
check(collectText(element("detail-risk-badges")).includes("Discovery candidate"), "an unlinked company is still a discovery candidate");

// The watchlist is a research queue sorted by name; the scores that used to order it are not shown.
vm.runInContext(`
  const withMetrics = (company) => Object.assign(company, { investor_metrics: { total_links: 1, upstream_count: 1, downstream_count: 0, concentration_score: 1, risk_score: 47 } });
  withMetrics(allCompanies[0]);
  withMetrics(allCompanies[1]);
  watchlist = new Set(["AMAT", "AMD", "ACME"]);
  renderWatchlistView();
`, context);
const queueCards = element("watchlist-grid").children.map(collectText);
check(queueCards.map((text) => text.split(" - ")[0]).join() === "ACME,AMD,AMAT", `watchlist order (Acme Retail, Advanced Micro Devices, Applied Materials: by name, not by ticker or links): ${queueCards.join(" | ")}`);
check(queueCards[0].includes("1 tracked link: 1 supplier, 0 customers.") && queueCards[2].includes("No tracked links yet"), `watchlist cards: ${queueCards.join(" | ")}`);
check(element("watchlist-count").textContent === "3 saved · sorted by name", `the watchlist says how it is sorted: ${element("watchlist-count").textContent}`);
check(!/Data gap|Larger-side|supplier risk|customer risk/i.test(queueCards.join(" ")), "the watchlist shows none of the removed scores");

vm.runInContext(`renderSectorView("Consumer");`, context);
check(collectText(element("sector-summary")) === "Companies1Linked companies1Supply links1", `sector summary: ${collectText(element("sector-summary"))}`);
vm.runInContext(`globalData.Bare = [allCompanies[2]]; renderSectorView("Bare"); delete globalData.Bare;`, context);
check(collectText(element("sector-summary")) === "Companies1Linked companies0Supply links0", `a sector with no linked companies: ${collectText(element("sector-summary"))}`);

element("compare-a").value = "ACME";
element("compare-b").value = "AMAT";
vm.runInContext("renderCompareView(false);", context);
const compareText = collectText(element("compare-grid"));
["Tracked links1", "Tracked links0", "Suppliers1", "Customers0"].forEach((expected) => {
  check(compareText.includes(expected), `compare card missing ${expected}; got ${compareText}`);
});
check(!/Larger-side|Data gap|Risk score|Supplier risk|Customer risk/.test(compareText), "compare no longer shows the removed scores");
vm.runInContext("watchlist = new Set();", context);

// --- Keyboard and screen-reader structure --------------------------------------------------
check(/<a class="skip-link" href="#main-content" onclick="skipToContent\(event\)">/.test(htmlSource) && /<main class="container" id="main-content" tabindex="-1">/.test(htmlSource), "a skip link leads to the main area");
check(htmlSource.indexOf('class="skip-link"') < htmlSource.indexOf('<header class="topbar">'), "the skip link comes before the navigation");
["sector", "watchlist", "predictions", "compare", "exposure", "companies", "details"].forEach((view) => {
  const start = htmlSource.indexOf(`<section id="view-${view}"`);
  check(start > 0 && /^\s*<h1 class="visually-hidden"/.test(htmlSource.slice(htmlSource.indexOf(">", start) + 1, start + 400)), `view-${view} needs a level-one heading`);
});
check(/id="detail-summary"[^>]*tabindex="0"/.test(htmlSource), "the scrollable profile must be reachable by keyboard");
vm.runInContext("globalThis.__skipPrevented = false; skipToContent({ preventDefault() { globalThis.__skipPrevented = true; } });", context);
check(context.__skipPrevented && context.document.activeElement.id === "main-content", "the skip link moves focus without touching the hash (the hash is the router)");

// --- Modified clicks on a company name belong to the browser ----------------------------
vm.runInContext("globalThis.__row = renderCompanyTableRow(allCompanies[0]); currentRoute = { view: 'companies' };", context);
const nameLink = context.__row.children[0].children[0].children[0];
["ctrlKey", "metaKey", "shiftKey", "altKey"].forEach((modifier) => {
  let stopped = false;
  let prevented = false;
  const pushedBefore = context.history.pushed.length;
  const event = { [modifier]: true, stopPropagation() { stopped = true; }, preventDefault() { prevented = true; } };
  nameLink.onclick(event);
  if (!stopped) context.__row.onclick(event); // what bubbling would do
  check(stopped && !prevented, `${modifier}-click must reach the browser untouched (stopped=${stopped}, prevented=${prevented})`);
  check(context.history.pushed.length === pushedBefore && vm.runInContext("currentRoute.view", context) === "companies", `${modifier}-click must not navigate this tab`);
});
{
  let stopped = false;
  const event = { stopPropagation() { stopped = true; }, preventDefault() {} };
  nameLink.onclick(event);
  if (!stopped) context.__row.onclick(event);
  check(vm.runInContext("currentRoute.view + ':' + currentRoute.ticker", context) === "company:AMD", "a plain click opens the company");
}

// --- Sector routes only know sectors ----------------------------------------------------
const noticeHost = mockElement("main");
noticeHost.appendChild(element("view-hero"));
const notices = () => noticeHost.children.filter((child) => child.className === "route-notice");
["constructor", "__proto__", "toString", "hasOwnProperty", "Nonexistent", "Linked Companies", ""].forEach((name) => {
  const before = notices().length;
  vm.runInContext(`applyRoute({ view: "sector", sector: ${JSON.stringify(name)} });`, context);
  check(vm.runInContext("currentRoute.view", context) === "overview", `#sector?sector=${name} falls back to the overview`);
  check(notices().length === before + 1 && collectText(notices()[notices().length - 1]).includes(`No sector in the dataset is named "${name}"`), `#sector?sector=${name} says why`);
});
vm.runInContext('renderSectorView("constructor"); renderSectorView("__proto__");', context); // must not throw
const noticesBefore = notices().length;
vm.runInContext('applyRoute({ view: "sector", sector: "Technology" });', context);
check(vm.runInContext("currentRoute.view", context) === "sector" && element("sector-title").textContent === "Technology" && notices().length === noticesBefore, "a real sector still renders");
check(element("sector-heading").textContent === "Technology sector", "the sector view's h1 names the sector");

vm.runInContext(`
  allCompanies.push({ ticker: "NVS", name: "Novartis", sector: "Linked Companies", upstream: [{ ticker: "TSM", name: "TSMC", type: "Foundry" }], downstream: [], connection_count: 1 });
  document.getElementById("exposure-input").value = "TSM";
  renderExposureView(false);
`, context);
const exposureChips = collectText(element("exposure-sectors"));
check(exposureChips.includes("Technology") && !exposureChips.includes("Linked Companies"), `exposure must not offer the synthetic bucket as a sector: ${exposureChips}`);
check(collectText(element("exposure-results")).includes("NVS"), "the company itself is still listed as exposed");
vm.runInContext("allCompanies = allCompanies.filter(company => company.ticker !== 'NVS');", context);

// --- Back and filters keep their place ---------------------------------------------------
vm.runInContext("currentRoute = { view: 'predictions' }; navigateCompany('AMD'); globalThis.__predictionsLabel = document.getElementById('detail-back-button').textContent; navigateBackToCompanies(); globalThis.__afterPredictionsBack = currentRoute.view;", context);
check(context.__predictionsLabel === "Back to predictions" && context.__afterPredictionsBack === "predictions", `Open brief from Predictions: "${context.__predictionsLabel}" returns to ${context.__afterPredictionsBack}`);

vm.runInContext(`
  currentRoute = { view: 'companies', query: 'amd' };
  navigateCompany('AMD');
  navigateCompany('IBM');
  globalThis.__chainPrevious = currentRoute.previous;
  globalThis.__chainLabel = document.getElementById('detail-back-button').textContent;
  navigateBackToCompanies();
  globalThis.__chainBack = currentRoute;
`, context);
check(context.__chainPrevious.view === "companies" && context.__chainPrevious.query === "amd", `a related company keeps the list the visitor came from: ${JSON.stringify(context.__chainPrevious)}`);
check(context.__chainBack.view === "companies" && context.__chainBack.query === "amd" && context.__chainLabel === "Back to screener", `Back to screener restores the query: ${JSON.stringify(context.__chainBack)}`);

{
  const pushedBefore = context.history.pushed.length;
  const replacedBefore = context.history.replaced.length;
  vm.runInContext("currentRoute = { view: 'overview' }; window.location.hash = '#overview'; clearFilters();", context);
  check(context.history.pushed.length === pushedBefore + 1 && context.history.pushed[pushedBefore] === "#companies" && context.history.replaced.length === replacedBefore,
    "Reset filters on the overview opens the screener as a new history entry, so Back returns to the overview");
  vm.runInContext("currentRoute = { view: 'companies', query: 'x' }; window.location.hash = '#companies?query=x'; clearFilters();", context);
  check(context.history.pushed.length === pushedBefore + 1 && context.history.replaced.length === replacedBefore + 1,
    "Reset filters inside the screener replaces the filtered entry");
}

vm.runInContext("window.location.hash = '#companies?query=zz'; applyRouteFromHash(false); globalThis.__zzRoute = currentRoute;", context);
check(context.__zzRoute.view === "companies" && context.__zzRoute.query === "zz", `#companies?query=zz stays the screener: ${JSON.stringify(context.__zzRoute)}`);
check(element("result-count").textContent === "0 results" && !element("view-companies").classList.contains("hidden") && element("view-industries").classList.contains("hidden"),
  "#companies?query=zz shows the screener's empty result, not the overview");

// --- The evidence dialog does not outlive its view ---------------------------------------
const dialogOpener = mockElement("button", "dialog-opener");
context.document.activeElement = dialogOpener;
vm.runInContext('openEvidenceModal({ ticker: "TSM", name: "TSMC", type: "Foundry" }, { ticker: "AMD" }, "upstream");', context);
check(!element("evidence-modal").classList.contains("hidden"), "the dialog opens");
vm.runInContext('applyRoute({ view: "predictions" });', context);
check(element("evidence-modal").classList.contains("hidden"), "Back / a route change closes the evidence dialog");
check(context.document.activeElement.id === "main-content", `focus goes to the main area, not a hidden opener: ${context.document.activeElement && context.document.activeElement.id}`);
[["detached", (node) => { node.isConnected = false; }], ["in a hidden view", (node) => { node.getClientRects = () => []; }]].forEach(([name, hide]) => {
  const gone = mockElement("button", "gone-opener");
  hide(gone);
  context.document.activeElement = gone;
  vm.runInContext('openEvidenceModal({ ticker: "TSM", name: "TSMC", type: "Foundry" }, { ticker: "AMD" }, "upstream");', context);
  context.document.activeElement = element("evidence-dialog");
  dispatchKeydown({ key: "Escape" });
  check(context.document.activeElement.id === "main-content", `Escape with an opener that is ${name} must not lose focus`);
});

// --- Two tabs share one watchlist ---------------------------------------------------------
storageValues.set("hephaestus_watchlist", '["AAPL"]'); // tab one tracked AAPL after this tab loaded
vm.runInContext("watchlist = new Set(); currentRoute = { view: 'company', ticker: 'MSFT' }; toggleCurrentWatchlist();", context);
check(storageValues.get("hephaestus_watchlist") === '["AAPL","MSFT"]', `tracking in a second tab keeps the first tab's company: ${storageValues.get("hephaestus_watchlist")}`);
vm.runInContext("toggleCurrentWatchlist();", context);
check(storageValues.get("hephaestus_watchlist") === '["AAPL"]', `untracking removes only that company: ${storageValues.get("hephaestus_watchlist")}`);
storageValues.set("hephaestus_watchlist", '["NVDA","AMD"]');
vm.runInContext("currentRoute = { view: 'watchlist' };", context);
windowHandlers.storage.forEach((handler) => handler({ key: "hephaestus_watchlist" }));
check(vm.runInContext("[...watchlist].sort().join()", context) === "AMD,NVDA" && element("watchlist-count").textContent.startsWith("1 saved"), "another tab's change shows up here");
storageValues.set("hephaestus_watchlist", '["ZZZ"]');
windowHandlers.storage.forEach((handler) => handler({ key: "something_else" }));
check(vm.runInContext("[...watchlist].sort().join()", context) === "AMD,NVDA", "other storage keys are ignored");
vm.runInContext("watchlist = new Set();", context);

// --- analytics.js hears about every page the app shows -------------------------------------
dispatchedEvents.length = 0;
vm.runInContext("window.location.hash = '#overview'; setRoute({ view: 'watchlist' });", context);
check(dispatchedEvents.join() === "hephaestus:route", `a navigation announces itself once: ${dispatchedEvents}`);
dispatchedEvents.length = 0;
vm.runInContext("updateRouteHash({ view: 'exposure', ticker: 'TS' }, false);", context);
check(dispatchedEvents.length === 0, "typing in a box rewrites the hash without announcing a page");
vm.runInContext("window.location.hash = '#overview'; updateRouteHash({ view: 'companies', query: 'nv' }, true);", context);
check(dispatchedEvents.join() === "hephaestus:route", "a search that opens the screener is a page");
dispatchedEvents.length = 0;
vm.runInContext("window.location.hash = '#predictions'; handleLocationChange();", context);
check(dispatchedEvents.join() === "hephaestus:route", "Back / Forward through the location guard announces once");

// --- the status line says when the daily update has stopped or discovery did not finish ------
// server1 was offline on 2026-10-17 and the site gave no sign that its data had stopped moving.
const statusNow = Date.parse("2026-10-17T12:00:00Z");
const statusLabel = (status) => context.pipelineStatusLabel(status, statusNow);
const freshStatus = { data_as_of: "2026-10-17T08:00:00+00:00", companies_researched: 86, new_links: 1, filings_swept: 1000, discovery: "ran", discovery_at: "2026-10-17T07:50:00+00:00" };
check(statusLabel(freshStatus) === "Updated Oct 17, 2026 · 86 companies researched · 1 new link · 1,000 filings swept", `a fresh run reads as before: ${statusLabel(freshStatus)}`);
check(!context.pipelineStatusIsStale(freshStatus, statusNow), "a run from this morning is not stale");
const dayOld = { ...freshStatus, data_as_of: "2026-10-16T08:07:08+00:00" };
check(!context.pipelineStatusIsStale(dayOld, statusNow) && !/old/.test(statusLabel(dayOld)), "yesterday's publish is normal (the retry run can be hours late)");
const threeDaysOld = { ...freshStatus, data_as_of: "2026-10-14T09:00:00+00:00" };
check(context.pipelineStatusIsStale(threeDaysOld, statusNow), "data older than 48 hours is stale");
check(/data is 3 days old: the daily update has not published/.test(statusLabel(threeDaysOld)), `a stale label names the age: ${statusLabel(threeDaysOld)}`);
check(!context.pipelineStatusIsStale({ data_as_of: "2026-10-15T13:00:00Z" }, statusNow) && context.pipelineStatusIsStale({ data_as_of: "2026-10-15T11:00:00Z" }, statusNow), "the limit is 48 hours");
check(!context.pipelineStatusIsStale({}, statusNow) && !context.pipelineStatusIsStale({ data_as_of: "not a date" }, statusNow) && !context.pipelineStatusIsStale(null, statusNow), "a missing or unreadable date is never reported as stale");
const unfinished = { ...freshStatus, discovery: "stale", discovery_at: "2026-10-09T21:00:00+00:00", companies_researched: null, filings_swept: null };
check(/discovery did not finish \(last completed Oct 9, 2026\)/.test(statusLabel(unfinished)) && !/researched|swept/.test(statusLabel(unfinished)), `an unfinished discovery shows no counts and says so: ${statusLabel(unfinished)}`);
check(/discovery did not finish$/.test(statusLabel({ ...unfinished, discovery_at: null })), "an unfinished discovery without a date still says so");
check(/discovery counts from Oct 8, 2026/.test(statusLabel({ ...freshStatus, discovery: "not_run", discovery_at: "2026-10-08T07:50:00Z" })), "a run without a discovery step dates the carried-over counts");
check(!/discovery/.test(statusLabel({ data_as_of: "2026-10-17T08:00:00Z", discovery: "not_run" })), "carried-over counts with no date say nothing more");
check(statusLabel(null) === "" && statusLabel("x") === "", "no status, no line");
const statusLine = element("pipeline-status");
context.renderPipelineStatus({ ...freshStatus, data_as_of: new Date().toISOString() });
check(!statusLine.classList.contains("stale") && statusLine.hidden === false, "a fresh status is plain text");
context.renderPipelineStatus({ ...freshStatus, data_as_of: "2026-01-01T00:00:00Z" });
check(statusLine.classList.contains("stale"), "a stopped update is highlighted");
context.renderPipelineStatus({ ...freshStatus, data_as_of: new Date().toISOString(), discovery: "stale" });
check(statusLine.classList.contains("stale"), "an unfinished discovery is highlighted");
context.renderPipelineStatus(null);
check(statusLine.hidden === true && !statusLine.classList.contains("stale"), "no status hides the line and clears the highlight");

// --- Private companies ---------------------------------------------------------------------------
// Entries marked listing: "private" have no share price: every market field is null. They carry a
// badge wherever a name appears, a Listing filter, a profile instead of a chart, and no valuation.
const walkTree = (node, visit) => { visit(node); (node.children || []).forEach((child) => walkTree(child, visit)); };
const findAll = (node, predicate) => { const found = []; walkTree(node, (n) => { if (predicate(n)) found.push(n); }); return found; };
const badgesIn = (node) => findAll(node, (n) => n.className === "private-badge");
const BAD_TEXT = /NaN|undefined|\bnull\b|\$0\.00(?!\d)/;
const noBadText = (label, text) => check(!BAD_TEXT.test(text), `${label} must not render NaN / null / undefined / $0.00: ${text}`);

check(/<select id="listing-filter" onchange="applyFilters\(\)">\s*<option value="">All<\/option>\s*<option value="public">Public<\/option>\s*<option value="private">Private<\/option>\s*<\/select>/.test(htmlSource), "the search panel has a Listing filter (All / Public / Private)");
check(/<label for="listing-filter">Listing<\/label>/.test(htmlSource), "the Listing filter is labelled");
check(/id="search-helper"[^>]*>[^<]*private companies/i.test(htmlSource), "the search helper mentions private companies");
check(vm.runInContext("MARKET_ONLY_IDS", context).every((id) => htmlIds.has(id)), "every market-only element of the brief exists in the page");
check(htmlIds.has("detail-private-facts") && htmlIds.has("prediction-scope-note"), "the private profile and the predictions note exist in the page");
check(context.routeToHash({ view: "companies", listing: "private" }) === "#companies?listing=private" && context.hashToRoute("#companies?listing=private").listing === "private", "the listing filter rides in the route hash");

const marketFields = { price: null, change: null, market_cap: null, enterprise_value: null, trailing_pe: null, forward_pe: null, price_to_book: null, high_52w: null, low_52w: null, revenue: null, margin: null, target_price: null, employees: null, dividend: "N/A", recommendation: "N/A", ceo: "N/A" };
const link = (ticker, name, extra = {}) => ({ ticker, name, type: "Supply Link", product: "AI compute capacity", confidence: 0.9, review_status: "approved", source: "https://www.sec.gov/x", evidence_excerpt: "Both companies are named here.", last_verified: "2026-10-20", ...extra });
const listed = (name, ticker, cap, extra = {}) => ({ name, ticker, industry: "Semiconductors", price: 10, change: 1.5, market_cap: cap, trailing_pe: 20, last_updated: "2026-10-22", upstream: [], downstream: [], ...extra });
const privateCompany = (name, ticker, extra = {}) => ({ name, ticker, listing: "private", industry: "Artificial Intelligence", last_updated: "2026-12-31", upstream: [], downstream: [], ...marketFields, ...extra });
context.__fixture = {
  Technology: [
    listed("Advanced Micro Devices, Inc. Common Stock", "AMD", 9e11, { downstream: [link("OPENAI", "OpenAI", { relationship_key: "AMD->OPENAI:SUPPLY" })] }),
    listed("Microsoft Corporation", "MSFT", 3e12),
    privateCompany("OpenAI", "OPENAI", {
      website: "https://www.openai.com/", hq: "San Francisco, California, USA", founded: 2015, aliases: ["OpenAI, Inc.", "OpenAI Global, LLC"],
      summary: "AI research and products company; maker of ChatGPT.",
      upstream: [link("AMD", "Advanced Micro Devices", { relationship_key: "AMD->OPENAI:SUPPLY" }), link("ORCL", "Oracle Corp", { relationship_key: "ORCL->OPENAI:SUPPLY" })],
    }),
    privateCompany("Bad Site Co", "BADSITE", { industry: "Biotechnology", website: "javascript:alert(1)", hq: null, founded: null, summary: "A private company with an unsafe website value.", investor_metrics: {} }),
    listed("Oracle Corp", "ORCL", 5e11, { downstream: [link("OPENAI", "OpenAI", { relationship_key: "ORCL->OPENAI:SUPPLY" })] }),
  ],
  Healthcare: [listed("Zeta Pharma", "ZETA", 1e9, { industry: "Drugs" }), privateCompany("Apex Private Labs", "APEXLABS", { industry: "Biotechnology", summary: "Private lab." })],
  Industrials: [privateCompany("Koch Industries", "KOCHIND", { industry: "Conglomerates", summary: "Private conglomerate." })],
  Energy: [listed("Exxon Mobil", "XOM", 4e11, { industry: "Oil", change: -2.1 })],
};
vm.runInContext(`
  globalData = globalThis.__fixture;
  hydrateCompanies(globalData);
  dashboardMeta = { investor_metrics: { unique_links: 2 } };
  watchlist = new Set();
  predictionData = { predictions: [], calibration: {} };
`, context);

// One badge component: a "Private" pill that says why there is no price.
vm.runInContext("globalThis.__badge = makePrivateBadge();", context);
check(context.__badge.className === "private-badge" && context.__badge.textContent === "Private" && context.__badge.title === "Private company: no public share price", "the Private badge is one component with an explanation");
check(vm.runInContext("isPrivate(getCompanyByTicker('openai')) && !isPrivate(getCompanyByTicker('AMD')) && !isPrivate(null) && !isPrivate({ listing: 'public' })", context), "only listing: private marks a private company");

// Screener rows: badge after the name, N/A in every market cell, no badge on a listed company.
vm.runInContext("globalThis.__privateRow = renderCompanyTableRow(getCompanyByTicker('OPENAI')); globalThis.__publicRow = renderCompanyTableRow(getCompanyByTicker('AMD'));", context);
check(badgesIn(context.__privateRow.children[0]).length === 1 && badgesIn(context.__publicRow).length === 0, "a private row carries the badge, a listed row does not");
check(context.__privateRow.children[0].children[0].children[0].children[0].textContent === "OpenAI", "the badge sits beside the company link");
const privateCells = context.__privateRow.children.map(collectText);
check(privateCells[2] === "N/A" && privateCells[3] === "N/A" && privateCells[4] === "N/A" && privateCells[6] === "N/A", `price, change, market cap and P/E are N/A for a private company: ${privateCells.join(" | ")}`);
noBadText("a private screener row", privateCells.join(" | "));

// The Listing filter: honoured by the filter, written to the hash, read back from it.
const setFilters = (values) => {
  element("search-input").value = values.query || "";
  element("sector-filter").value = values.sector || "";
  element("dependency-filter").value = values.dependency || "";
  element("listing-filter").value = values.listing || "";
  element("connected-filter").checked = Boolean(values.connected);
};
const screenerTickers = () => vm.runInContext("currentCompaniesList.map(company => company.ticker).sort().join()", context);
setFilters({});
vm.runInContext("currentRoute = { view: 'overview' }; window.location.hash = '#overview'; applyFilters(true);", context);
check(screenerTickers() === "AMD,APEXLABS,BADSITE,KOCHIND,MSFT,OPENAI,ORCL,XOM,ZETA", `All lists listed and private companies together: ${screenerTickers()}`);
setFilters({ listing: "private" });
vm.runInContext("applyFilters(true);", context);
check(screenerTickers() === "APEXLABS,BADSITE,KOCHIND,OPENAI", `Private lists only private companies: ${screenerTickers()}`);
check(context.window.location.hash === "#companies?listing=private" && vm.runInContext("currentRoute.listing", context) === "private", `the listing is written to the hash: ${context.window.location.hash}`);
check(element("current-industry-title").textContent === "Private companies" && element("result-count").textContent === "4 results", "the screener says what it lists");
setFilters({ listing: "public" });
vm.runInContext("applyFilters(true);", context);
check(screenerTickers() === "AMD,MSFT,ORCL,XOM,ZETA" && context.window.location.hash === "#companies?listing=public", `Public lists only listed companies: ${screenerTickers()} ${context.window.location.hash}`);
setFilters({ listing: "private", sector: "Technology" });
vm.runInContext("applyFilters(true);", context);
check(screenerTickers() === "BADSITE,OPENAI" && element("current-industry-title").textContent === "Technology" && context.window.location.hash === "#companies?sector=Technology&listing=private", `the listing combines with the sector filter: ${screenerTickers()} ${context.window.location.hash}`);

// A link restores the filter on load and on Back/Forward; an unknown value means all.
vm.runInContext("window.location.hash = '#companies?listing=private'; applyRouteFromHash(false);", context);
check(element("listing-filter").value === "private" && screenerTickers() === "APEXLABS,BADSITE,KOCHIND,OPENAI", "#companies?listing=private is honoured on load");
vm.runInContext("window.location.hash = '#companies?listing=public'; handleLocationChange();", context);
check(element("listing-filter").value === "public" && screenerTickers() === "AMD,MSFT,ORCL,XOM,ZETA", "Back/Forward re-applies the listing in the hash");
vm.runInContext("window.location.hash = '#companies?listing=bogus'; handleLocationChange();", context);
check(element("listing-filter").value === "" && screenerTickers().split(",").length === 9, "an unknown listing value in a link means all companies");

// Reset filters clears it (in the screener and from any other view).
setFilters({ listing: "private" });
vm.runInContext("currentRoute = { view: 'companies', listing: 'private' }; window.location.hash = '#companies?listing=private'; clearFilters();", context);
check(element("listing-filter").value === "" && context.window.location.hash === "#companies" && screenerTickers().split(",").length === 9, `Reset filters clears the listing: ${context.window.location.hash}`);
setFilters({ listing: "private" });
vm.runInContext("resetDashboard();", context);
check(element("listing-filter").value === "", "resetting the dashboard clears the listing");

// With a listing chosen, an exact ticker does not jump past the filter.
setFilters({ query: "OPENAI", listing: "public" });
vm.runInContext("currentRoute = { view: 'companies' }; applyFilters(true, true, true);", context);
check(vm.runInContext("currentRoute.view", context) === "companies" && screenerTickers() === "AMD,ORCL", `Public + OPENAI lists the listed companies that name it, and does not open the private company: ${screenerTickers()}`);
setFilters({ query: "OPENAI" });
vm.runInContext("applyFilters(true, true, true);", context);
check(vm.runInContext("currentRoute.view + ':' + currentRoute.ticker", context) === "company:OPENAI", "an exact private id still opens its brief");
element("search-input").value = "";
vm.runInContext("updateSearchHelper();", context);
check(/private companies/.test(element("search-helper").textContent), `search helper: ${element("search-helper").textContent}`);
element("search-input").value = "OPENAI";
vm.runInContext("updateSearchHelper();", context);
check(element("search-helper").textContent.startsWith("Exact match: OpenAI, a private company."), `an exact private match is not called a ticker: ${element("search-helper").textContent}`);

// Search finds private companies by name and by alias.
setFilters({ query: "apex private" });
vm.runInContext("applyFilters(false);", context);
check(screenerTickers() === "APEXLABS", "search finds a private company by name");
setFilters({ query: "openai global" });
vm.runInContext("applyFilters(false);", context);
check(screenerTickers() === "OPENAI", `search finds a private company by an alias: ${screenerTickers()}`);
setFilters({ query: "OpenAI, Inc." });
vm.runInContext("applyFilters(false);", context);
check(screenerTickers() === "OPENAI", `an alias match ignores case: ${screenerTickers()}`);
vm.runInContext("getCompanyByTicker('AMD').aliases = 'not a list'; getCompanyByTicker('ORCL').aliases = [null, 7];", context);
setFilters({ query: "oracle" });
vm.runInContext("applyFilters(false);", context);
check(screenerTickers().includes("ORCL"), "a malformed aliases value does not break the search");
vm.runInContext("delete getCompanyByTicker('AMD').aliases; delete getCompanyByTicker('ORCL').aliases;", context);

// Sorting keeps the N/A cells last in both directions, for every market column.
setFilters({});
vm.runInContext("applyFilters(false);", context);
["market_cap", "price", "change", "trailing_pe"].forEach((column) => {
  [true, false].forEach((asc) => {
    vm.runInContext(`sortCol = '${column}'; sortAsc = ${asc}; renderLevel2();`, context);
    const order = element("company-table-body").children.map((row) => collectText(row.children[1]));
    const privateIds = ["APEXLABS", "BADSITE", "KOCHIND", "OPENAI"];
    check(order.length === 9 && order.slice(order.length - 4).every((ticker) => privateIds.includes(ticker)), `private companies sort last by ${column} (${asc ? "ascending" : "descending"}): ${order.join()}`);
  });
});
vm.runInContext("sortCol = 'market_cap'; sortAsc = false;", context);

// The brief of a private company: a profile, not a chart or valuation.
let widgetCalls = 0;
context.window.TradingView = { widget: class { constructor() { widgetCalls += 1; } } };
context.window.setInterval = () => 1;
context.window.clearInterval = () => {};
vm.runInContext("currentRoute = { view: 'companies' }; navigateCompany('OPENAI');", context);
check(widgetCalls === 0, "no TradingView chart is created for a private company");
check(collectText(element("tv_chart_container")) === "Private company: no public share price" && element("tv_chart_container").classes.has("chart-private") && !element("tv_chart_container").classes.has("chart-loading"), `the chart area says why there is no chart: ${collectText(element("tv_chart_container"))}`);
check(vm.runInContext("MARKET_ONLY_IDS", context).every((id) => element(id).classes.has("hidden")), "the price, valuation, sentiment, CEO and headcount blocks are hidden");
check(!element("detail-private-facts").classes.has("hidden"), "the profile facts are shown");
const facts = collectText(element("detail-private-facts"));
["Sector: Technology", "Industry: Artificial Intelligence", "Headquarters: San Francisco, California, USA", "Founded: 2015", "Website: openai.com", "Also named: OpenAI, Inc.; OpenAI Global, LLC"].forEach((expected) => {
  check(facts.includes(expected), `private profile missing ${expected}; got ${facts}`);
});
const anchors = findAll(element("detail-private-facts"), (n) => n.tagName === "a");
check(anchors.length === 1 && anchors[0].href === "https://www.openai.com/" && anchors[0].rel === "noopener noreferrer" && anchors[0].target === "_blank", `the website is a safe external link: ${JSON.stringify(anchors.map((a) => [a.href, a.rel, a.target]))}`);
check(element("detail-summary").textContent === "AI research and products company; maker of ChatGPT.", "the registry description is the summary");
check(element("detail-name").textContent === "OpenAI" && badgesIn(element("detail-name")).length === 1, "the detail header carries the badge");
check(element("detail-heading").textContent === "OpenAI (private company) suppliers and customers" && context.document.title === "OpenAI (private company) suppliers and customers | Hephaestus", `the heading names it a private company: ${element("detail-heading").textContent} / ${context.document.title}`);
check(element("brief-confidence").textContent === "2" && element("brief-approved").textContent === "0", "the decision brief shows supplier and customer counts");
const briefText = ["brief-confidence", "brief-approved", "detail-risk-badges", "detail-related", "detail-story"].map((id) => collectText(element(id))).join(" | ");
check(!/valuation|market cap|price|worth|\bP\/E\b/i.test(briefText) && !/%/.test(briefText), `the brief has no valuation language: ${briefText}`);
check(findAll(element("detail-upstream"), (n) => n.className === "xray-card upstream").length === 2 && findAll(element("supply-graph"), (n) => (n.className || "").startsWith("graph-node")).length >= 3 && badgesIn(findAll(element("supply-graph"), (n) => n.className === "graph-node graph-center")[0]).length === 1, "the supplier X-Ray and graph are there, and the centre node is badged");
["detail-name", "detail-heading", "detail-industry", "detail-ticker", "detail-story", "detail-private-facts", "detail-summary", "tv_chart_container", "detail-related", "supply-graph", "detail-upstream", "detail-downstream", "detail-risk-badges", "detail-price", "detail-change", "detail-high", "detail-mcap", "detail-ev", "detail-tpe", "detail-fpe", "detail-rev", "detail-margin", "detail-target", "detail-pb", "detail-ceo", "detail-emp"].forEach((id) => noBadText(`private brief ${id}`, collectText(element(id))));

// Track, Compare and Exposure work on it.
vm.runInContext("toggleCurrentWatchlist();", context);
check(vm.runInContext("watchlist.has('OPENAI')", context) && element("watchlist-toggle").textContent === "Tracking", "a private company can be tracked");
vm.runInContext("navigateCompareFromCurrent(); globalThis.__cmpRoute = currentRoute;", context);
check(context.__cmpRoute.view === "compare" && context.__cmpRoute.a === "OPENAI" && context.__cmpRoute.b === "AMD", `Compare starts from the private company: ${JSON.stringify(context.__cmpRoute)}`);
vm.runInContext("navigateCompany('OPENAI'); navigateExposureFromCurrent(); globalThis.__expRoute = currentRoute;", context);
check(context.__expRoute.view === "exposure" && context.__expRoute.ticker === "OPENAI", "Exposure starts from the private company");

// A website value that is not http(s) is never linked or shown; empty profile fields are left out.
vm.runInContext("navigateCompany('BADSITE');", context);
const badSiteFacts = collectText(element("detail-private-facts"));
check(findAll(element("detail-private-facts"), (n) => n.tagName === "a").length === 0 && !/javascript|Website|Headquarters|Founded|Also named/.test(badSiteFacts) && badSiteFacts.includes("Sector: Technology"), `an unsafe website is dropped: ${badSiteFacts}`);
check(collectText(element("detail-risk-badges")).includes("Discovery candidate"), "a private company with no links is a discovery candidate like any other");
noBadText("a private brief with no profile fields", badSiteFacts + collectText(element("detail-story")));

// ...and the brief of a listed company is whole again (and the chart is drawn).
vm.runInContext("navigateCompany('AMD');", context);
check(widgetCalls === 1, "a listed company still gets its TradingView chart");
check(!element("tv_chart_container").classes.has("chart-private") && vm.runInContext("MARKET_ONLY_IDS", context).every((id) => !element(id).classes.has("hidden")) && element("detail-private-facts").classes.has("hidden"), "opening a listed company after a private one restores the market blocks");
check(badgesIn(element("detail-name")).length === 0 && element("detail-price").textContent === "$10.00" && element("detail-mcap").textContent === "$900.00B", "the listed company's header has no badge and its metrics are filled");
const listedRelated = findAll(element("detail-related"), (n) => n.className === "related-chip");
check(listedRelated.length === 1 && listedRelated[0].textContent === "OPENAI" && badgesIn(listedRelated[0]).length === 1, "a related-company chip for a private company carries the badge");
check(findAll(element("detail-downstream"), (n) => n.className === "xray-company-line").filter((line) => badgesIn(line).length).length === 1, "the X-Ray card of a private counterparty carries the badge");
check(findAll(element("supply-graph"), (n) => (n.className || "").startsWith("graph-node ") && badgesIn(n).length).length === 1, "the graph node of a private counterparty carries the badge");
vm.runInContext("openEvidenceModal(getCompanyByTicker('AMD').downstream[0], getCompanyByTicker('AMD'), 'downstream');", context);
check(badgesIn(element("modal-body")).length === 1 && collectText(element("modal-body")).includes("OpenAI (OPENAI)"), "the evidence dialog marks a private counterparty");
vm.runInContext("closeEvidenceModal(); watchlist = new Set();", context);

// Chips elsewhere (change feed, exposure rows, top briefs) use the same component.
vm.runInContext("globalThis.__chips = [renderTickerChip('OPENAI'), renderTickerChip('AMD'), renderTickerChip('NOSUCH')];", context);
check(badgesIn(context.__chips[0]).length === 1 && badgesIn(context.__chips[1]).length === 0 && badgesIn(context.__chips[2]).length === 0, "ticker chips badge private companies only");
vm.runInContext("dashboardMeta = { investor_metrics: { unique_links: 2, most_connected: [{ ticker: 'OPENAI' }, { ticker: 'AMD' }] } }; renderQuickActions();", context);
check(element("quick-actions").children.filter((chip) => badgesIn(chip).length).map((chip) => chip.textContent).join() === "OPENAI", "the top-brief chips badge private companies only");
vm.runInContext("dashboardMeta = { investor_metrics: { unique_links: 2 } };", context);

// Overview: the counts are kept apart.
vm.runInContext("renderOverviewStats();", context);
const privateOverview = collectText(element("overview-stats"));
check(privateOverview.includes("5Public companies") && privateOverview.includes("4Private companies") && !privateOverview.includes("9Companies"), `overview counts public and private apart: ${privateOverview}`);
vm.runInContext("renderLevel1();", context);
const sectorCards = Object.fromEntries(element("industry-grid").children.map((card) => [collectText(card.children[0]), collectText(card.children[1])]));
check(sectorCards.Technology === "3 public companies and 2 private · 2 links" && sectorCards.Healthcare === "1 public company and 1 private · 0 links" && sectorCards.Industrials === "1 private company · 0 links" && sectorCards.Energy === "1 company · 0 links", `sector cards count each kind: ${JSON.stringify(sectorCards)}`);
vm.runInContext("globalThis.__saved = allCompanies.slice(); allCompanies = allCompanies.filter(company => company.listing !== 'private'); renderOverviewStats(); allCompanies = globalThis.__saved;", context);
check(collectText(element("overview-stats")).includes("5Companies") && !collectText(element("overview-stats")).includes("Private"), "with no private company the overview reads as before");
check(vm.runInContext("getLastUpdatedLabel()", context) === "Data synced Oct 22, 2026", `a private company's registry date does not set "Data synced": ${vm.runInContext("getLastUpdatedLabel()", context)}`);

// Sector page: counts, a private card only where there are private companies, private rows after listed ones.
vm.runInContext("renderSectorView('Technology');", context);
check(collectText(element("sector-summary")) === "Public companies3Private companies2Linked companies3Supply links2" && element("sector-count").textContent === "3 public companies and 2 private", `sector summary: ${collectText(element("sector-summary"))} / ${element("sector-count").textContent}`);
// Oracle has no market cap here (it comes after the private companies in the data): it still sorts before them.
vm.runInContext("globalData.Technology.find(company => company.ticker === 'ORCL').market_cap = null; renderSectorView('Technology'); globalData.Technology.find(company => company.ticker === 'ORCL').market_cap = 5e11;", context);
const sectorOrder = element("sector-company-table-body").children.map((row) => collectText(row.children[1]));
check(sectorOrder.slice(0, 3).join() === "MSFT,AMD,ORCL" && sectorOrder.slice(3).sort().join() === "BADSITE,OPENAI", `sector rows: listed by market cap, then private: ${sectorOrder.join()}`);
vm.runInContext("renderSectorView('Technology');", context);
check(element("sector-company-table-body").children.filter((row) => badgesIn(row).length).length === 2, "private sector rows are badged");
vm.runInContext("renderSectorView('Energy');", context);
check(collectText(element("sector-summary")) === "Companies1Linked companies0Supply links0", "a sector without private companies has no private card");
vm.runInContext("renderSectorView('Industrials');", context);
check(collectText(element("sector-summary")).startsWith("Public companies0Private companies1") && element("sector-count").textContent === "1 private company", "a sector with only private companies counts them honestly");
["sector-summary", "sector-count", "sector-company-table-body"].forEach((id) => noBadText(`sector view ${id}`, collectText(element(id))));

// Watchlist, compare and exposure.
vm.runInContext("watchlist = new Set(['OPENAI', 'AMD', 'BADSITE']); renderWatchlistView();", context);
const savedCards = element("watchlist-grid").children;
check(savedCards.map((card) => collectText(card.children[0].children[0]).split(" - ")[0]).join() === "AMD,BADSITE,OPENAI", `watchlist is sorted by name: ${savedCards.map(collectText).join(" | ")}`);
check(savedCards.map((card) => badgesIn(card).length).join() === "0,1,1", "watchlist cards badge private companies");
noBadText("the watchlist", collectText(element("watchlist-grid")));
vm.runInContext("watchlist = new Set();", context);
element("compare-a").value = "OPENAI";
element("compare-b").value = "AMD";
vm.runInContext("renderCompareView(false);", context);
const compareCards = element("compare-grid").children;
check(badgesIn(compareCards[0]).length === 1 && badgesIn(compareCards[1]).length === 0, "compare badges the private company only");
check(collectText(compareCards[0]).includes("Market capN/A") && collectText(compareCards[1]).includes("Market cap$900.00B") && collectText(compareCards[0]).includes("Suppliers2"), `compare shows N/A for a private market cap: ${collectText(compareCards[0])}`);
noBadText("compare", collectText(element("compare-grid")));
element("compare-a").value = "BADSITE";
element("compare-b").value = "KOCHIND";
vm.runInContext("renderCompareView(false);", context);
noBadText("compare of two private companies", collectText(element("compare-grid")));
check(badgesIn(element("compare-grid")).length === 2, "both private cards are badged");
element("exposure-input").value = "OPENAI";
vm.runInContext("renderExposureView(false);", context);
check(badgesIn(element("exposure-title")).length === 1 && element("exposure-title").textContent === "Who is exposed to OpenAI", "the exposure heading carries the badge");
element("exposure-input").value = "AMD";
vm.runInContext("renderExposureView(false);", context);
check(badgesIn(element("exposure-title")).length === 0 && badgesIn(element("exposure-results")).length === 1, "exposure rows badge the private dependent only");
["exposure-title", "exposure-count", "exposure-summary", "exposure-results", "exposure-sectors"].forEach((id) => noBadText(`exposure ${id}`, collectText(element(id))));

// Predictions: private companies are not scored, and the page says so only when there are any.
vm.runInContext("renderPredictionsView();", context);
check(element("prediction-scope-note").hidden === false, "the predictions page notes that private companies are not scored");
vm.runInContext("globalThis.__saved = allCompanies.slice(); allCompanies = allCompanies.filter(company => company.listing !== 'private'); renderPredictionsView(); globalThis.__noteHidden = document.getElementById('prediction-scope-note').hidden; allCompanies = globalThis.__saved;", context);
check(context.__noteHidden === true, "the note stays hidden while there are no private companies");
check(/not scored/.test(htmlSource.slice(htmlSource.indexOf('id="prediction-scope-note"'), htmlSource.indexOf('id="prediction-scope-note"') + 300)), "the note's text says private companies are not scored");
// The badge and the chart note are styled: the class names the script emits exist in the stylesheet.
const cssSource = fs.readFileSync("docs/styles.css", "utf8");
check(/\nspan\.private-badge \{[^}]*color: var\(--private\)[^}]*\}/.test(cssSource) && cssSource.includes(".chart-wrapper.chart-private") && cssSource.includes("--private-bg"), "styles.css styles the Private badge and the private chart note");
