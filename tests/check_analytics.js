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
    addEventListener: (type, fn) => { listeners[type] = fn; },
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
  const context = { window: undefined, document: undefined };
  vm.createContext(context);
  vm.runInContext(source.replace("const GOATCOUNTER_CODE = '';", `const GOATCOUNTER_CODE = '${code}';`), context);
  return { run: (expression) => vm.runInContext(expression, context), context, win, doc, appended, listeners };
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
on.win.location.hash = "#exposure?ticker=TSM";
on.listeners.hashchange();
assert(counted.join(" ") === "/Hephaestus/#company?ticker=NVDA /Hephaestus/#exposure?ticker=TSM", `counted ${counted.join(" ")}`);

console.log("Analytics checks passed");
