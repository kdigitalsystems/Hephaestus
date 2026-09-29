// Render scripts/og_image.html to docs/og-image.png (1200x630), the link-preview image.
// Needs Playwright with a Chromium build: npm install playwright && npx playwright install chromium
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const root = path.resolve(__dirname, "..");
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  await page.goto(`file://${path.join(root, "scripts", "og_image.html")}`);
  await page.screenshot({ path: path.join(root, "docs", "og-image.png"), clip: { x: 0, y: 0, width: 1200, height: 630 } });
  await browser.close();
  console.log("Wrote docs/og-image.png");
})();
