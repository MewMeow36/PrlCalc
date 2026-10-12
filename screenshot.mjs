import { chromium } from "playwright";

const url = process.argv[2] || "http://localhost:8000/";
const out = process.argv[3] || "shot.png";
const browser = await chromium.launch();

const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
desktop.on("console", m => { if (m.type() === "error") console.log("console error:", m.text()); });
desktop.on("pageerror", e => console.log("page error:", e.message));
const ready = () => document.getElementById("live")?.textContent.includes("updated");
await desktop.goto(url, { waitUntil: "load" });
await desktop.waitForFunction(ready, null, { timeout: 45000 });
await desktop.waitForTimeout(2000);
await desktop.screenshot({ path: out, fullPage: true });

await desktop.click('.tab[data-view="calc"]');
await desktop.waitForTimeout(1200);
await desktop.screenshot({ path: out.replace(/\.png$/, "-calc.png"), fullPage: true });
await desktop.click('.tab[data-view="dash"]');
await desktop.waitForTimeout(400);

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
await mobile.goto(url, { waitUntil: "load" });
await mobile.waitForFunction(ready, null, { timeout: 45000 });
await mobile.waitForTimeout(2000);
await mobile.screenshot({ path: out.replace(/\.png$/, "-mobile.png"), fullPage: true });

await browser.close();
console.log("saved", out, "and mobile variant");
