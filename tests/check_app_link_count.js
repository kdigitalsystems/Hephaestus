const fs = require("fs");

const data = JSON.parse(fs.readFileSync("docs/dashboard_data.json", "utf8"));
const allCompanies = Object.values(data.industries || {}).flat();
const relationshipKeys = new Set();

for (const company of allCompanies) {
  for (const link of [...(company.upstream || []), ...(company.downstream || [])]) {
    const key = link.relationship_key || link.edge_id;
    if (key !== undefined && key !== null) {
      relationshipKeys.add(String(key));
    }
  }
}

const publishedCount = Number(data.investor_metrics?.unique_links || 0);
if (publishedCount < 50) {
  throw new Error(`published link count is too low: ${publishedCount}`);
}

if (relationshipKeys.size !== publishedCount) {
  throw new Error(`dashboard unique link mismatch: JSON has ${relationshipKeys.size}, metrics report ${publishedCount}`);
}
