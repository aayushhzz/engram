// Renders composition/index.html frame by frame through headless Chrome (CDP).
// Usage: node capture.mjs <html> <outDir> <fps> <seconds>
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [htmlArg, outArg, fpsArg = "30", secArg = "19.968"] = process.argv.slice(2);
const html = resolve(htmlArg), out = resolve(outArg);
const fps = Number(fpsArg), total = Math.round(Number(secArg) * fps);
mkdirSync(out, { recursive: true });

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9333;
const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
  `--remote-debugging-port=${port}`, "--window-size=1920,1080",
  `--user-data-dir=${resolve(out, "..", "chrome-profile")}`, "about:blank",
], { stdio: "ignore" });

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function getTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find(t => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome did not expose a debugging port");
}

const ws = new WebSocket(await getTarget());
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0; const waiting = new Map();
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
};
const send = (method, params = {}) => new Promise(r => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });

await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
await send("Page.navigate", { url: pathToFileURL(html).href });
await sleep(1200);

for (let f = 0; f < total; f++) {
  const t = f / fps;
  await send("Runtime.evaluate", { expression: `window.renderAt(${t})`, awaitPromise: false });
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(resolve(out, `f${String(f).padStart(5, "0")}.png`), Buffer.from(shot.result.data, "base64"));
  if (f % 60 === 0) console.log(`frame ${f}/${total}`);
}
console.log(`done: ${total} frames`);
ws.close();
chrome.kill();
process.exit(0);
