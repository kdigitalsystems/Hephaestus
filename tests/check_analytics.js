// docs/analytics.js must stay inert until a GoatCounter code is set, and must never
// count visitors who ask not to be tracked.
const fs = require("fs");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = fs.readFileSync("docs/analytics.js", "utf8");
assert(/^const GOATCOUNTER_CODE = '';$/m.test(source), "the committed code must be empty: counting is the site owner's opt-in");
assert(!/document\.cookie|localStorage|sessionStorage/.test(source), "analytics must not store anything in the browser");

function load(code) {
  const appended = [];
  const listeners = {};
  const win = {
    navigator: {},
    location: { protocol: "https:", hostname: "kdigitalsystems.github.io", pathname: "/Hephaestus/", hash: "#company?ticker=NVDA" },
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  };
  const doc = {
    head: { appendChild: (node) => appended.push(node) },
    createElement: () => {
      const node = { attributes: {}, events: {} };
      node.setAttribute = (key, value) => { node.attributes[key] = value; };
      node.addEventListener = (type, fn) => { node.events[type] = fn; };
      return node;
    },
  };
  const context = { window: undefined, document: undefined, URLSearchParams, encodeURIComponent };
  vm.createContext(context);
  vm.runInContext(source.replace("const GOATCOUNTER_CODE = '';", `const GOATCOUNTER_CODE = '${code}';`), context);
  const fire = (type) => (listeners[type] || []).forEach((fn) => fn());
  return { run: (expression) => vm.runInContext(expression, context), context, win, doc, appended, listeners, fire };
}

const off = load("");
off.context.__win = off.win;
off.context.__doc = off.doc;
assert(off.run("startAnalytics(__win, __doc)") === false && off.appended.length === 0, "no code, no script");

const site = { protocol: "https:", hostname: "kdigitalsystems.github.io" };
const on = load("hephaestus");
on.context.__site = site;
assert(on.run("analyticsAllowed('hephaestus', {}, __site)") === true, "a valid code on the public site counts");
assert(on.run("analyticsAllowed('hephaestus', { doNotTrack: '1' }, __site)") === false, "Do Not Track is honoured");
assert(on.run("analyticsAllowed('hephaestus', { globalPrivacyControl: true }, __site)") === false, "Global Privacy Control is honoured");
assert(on.run("analyticsAllowed('hephaestus', {}, { protocol: 'http:', hostname: 'localhost' })") === false, "local copies are not counted");
assert(on.run("analyticsAllowed('evil.com/x', {}, __site)") === false, "the code cannot redirect the script elsewhere");
assert(on.run("analyticsPath({ pathname: '/Hephaestus/', hash: '#company?ticker=NVDA' })") === "/Hephaestus/#company?ticker=NVDA", "hash routes count as pages");
assert(on.run("analyticsPath({ pathname: '/Hephaestus/company/NVDA.html', hash: '' })") === "/Hephaestus/company/NVDA.html", "plain pages");

// Only the view and a few parameters are sent; what a visitor types into a box is never sent.
const sent = (hash) => on.run(`analyticsPath({ pathname: '/Hephaestus/', hash: ${JSON.stringify(hash)} })`);
assert(sent("#companies?query=nvidia%20supplier") === "/Hephaestus/#companies", `search terms must not be sent: ${sent("#companies?query=nvidia%20supplier")}`);
assert(sent("#companies?query=secret&sector=Technology&connected=1") === "/Hephaestus/#companies?sector=Technology", "the screener keeps its sector and drops the rest");
assert(sent("#exposure?ticker=tsm") === "/Hephaestus/#exposure?ticker=TSM", "tickers are normalised");
assert(sent("#exposure?ticker=my%20private%20idea") === "/Hephaestus/#exposure", "free text typed into the ticker box is not a ticker");
assert(sent("#compare?a=NVDA&b=AMD") === "/Hephaestus/#compare?a=NVDA&b=AMD", "both compared tickers are kept");
assert(sent("#compare?a=NVDA&b=a%20long%20phrase%20typed%20here") === "/Hephaestus/#compare?a=NVDA", "a free-text second box is dropped");
assert(sent("#sector?sector=Consumer%20Cyclical") === "/Hephaestus/#sector?sector=Consumer%20Cyclical", "sector pages count");
assert(sent("#company?ticker=NVDA&query=x&previous=y") === "/Hephaestus/#company?ticker=NVDA", "unknown parameters are dropped");
assert(sent("#overview") === "/Hephaestus/" && sent("") === "/Hephaestus/", "the bare address and #overview are one page");
assert(sent("#review") === "/Hephaestus/" && sent("#something-else?x=1") === "/Hephaestus/", "anchors and unknown views add nothing");
assert(["#companies?query=a", "#exposure?ticker=%3Cscript%3E", "#compare?a=1&b=2&query=q"].every((hash) => !/query|script|<|%3C/i.test(sent(hash))), "nothing from a query string leaks through");

on.context.__win = on.win;
on.context.__doc = on.doc;
assert(on.run("startAnalytics(__win, __doc)") === true && on.appended.length === 1, "one script is added");
const script = on.appended[0];
assert(script.src === "https://gc.zgo.at/count.js", `script source ${script.src}`);
assert(script.attributes["data-goatcounter"] === "https://hephaestus.goatcounter.com/count", "counts go to the configured site");
assert(on.win.goatcounter.no_onload === true, "the page view is counted by hand, with the hash route");
const counted = [];
on.win.goatcounter.count = (options) => counted.push(options.path);
script.events.load();
const go = (hash, ...events) => { on.win.location.hash = hash; events.forEach((type) => on.fire(type)); };

// Back/Forward fire hashchange and the app's route event: one count.
go("#exposure?ticker=TSM", "hashchange", "hephaestus:route");
assert(counted.join(" ") === "/Hephaestus/#company?ticker=NVDA /Hephaestus/#exposure?ticker=TSM", `counted ${counted.join(" ")}`);

// The app navigates with history.pushState, which fires no hashchange: only its own event reports the page.
go("#watchlist", "hephaestus:route");
go("#predictions", "hephaestus:route");
go("#company?ticker=AMD", "hephaestus:route");
assert(counted.slice(2).join(" ") === "/Hephaestus/#watchlist /Hephaestus/#predictions /Hephaestus/#company?ticker=AMD", `app navigation must be counted: ${counted.slice(2).join(" ")}`);

// The same page announced twice (a redirect, a repeated click) is counted once; going back to an earlier page is a new view.
go("#company?ticker=AMD", "hephaestus:route", "hephaestus:route");
go("#predictions", "hephaestus:route");
assert(counted.length === 6 && counted[5] === "/Hephaestus/#predictions", `de-duplication: ${counted.slice(5).join(" ")}`);

// Searching rewrites the hash with the query on every keystroke; nothing is sent and the view counts once.
go("#companies?query=nv", "hephaestus:route");
go("#companies?query=nvidia%20private", "hephaestus:route", "hashchange");
assert(counted.slice(6).join(" ") === "/Hephaestus/#companies", `a search is the screener, once: ${counted.slice(6).join(" ")}`);
assert(!counted.some((path) => /query|nvidia|private|=nv$/i.test(path)), `no search text reaches the counter: ${counted.join(" ")}`);

console.log("Analytics checks passed");
