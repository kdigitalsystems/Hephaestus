const fs = require("fs");
const vm = require("vm");

const appSource = fs.readFileSync("docs/app.js", "utf8");
const prefix = appSource.split("fetch('dashboard_data.json')")[0];

const context = {
  console,
  document: {
    createElement: () => ({
      appendChild() {},
      className: "",
      textContent: "",
    }),
    getElementById: () => ({ textContent: "" }),
  },
};
vm.createContext(context);
vm.runInContext(
  `${prefix}
   globalThis.__setCompanies = (companies) => { allCompanies = companies; };
   globalThis.__uniqueLinkCount = uniqueLinkCount;`,
  context,
);

context.__setCompanies([
  {
    upstream: [
      { edge_id: 1, relationship_key: "TSM->AMD:FOUNDRY" },
      { edge_id: 99, relationship_key: "TSM->AMD:FOUNDRY" },
    ],
    downstream: [{ edge_id: 2 }],
  },
  {
    upstream: [],
    downstream: [{ edge_id: 2 }],
  },
]);

const count = context.__uniqueLinkCount();
if (count !== 2) {
  throw new Error(`expected 2 unique links, got ${count}`);
}
