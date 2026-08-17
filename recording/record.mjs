/**
 * Records silent demo footage of the deployed blastradius console for voice-over.
 *
 * Timed to DEMO_SCRIPT.md, which measures 431 spoken words at 150 wpm = 2:52. Each beat below
 * is held for the duration of the narration that covers it, so the voice track lands without
 * re-cutting. Beat boundaries are taken straight from the script's section headings.
 *
 * Playwright rather than a screen capture: no menu bars, no OS cursor, no recording-permission
 * prompt, deterministic timing, and re-renderable if a beat needs to be longer or shorter.
 * The mouse is drawn in by hand as a red dot, because Playwright's synthetic pointer is
 * invisible to the video encoder and the hover interaction has to be legible.
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const URL_ = "https://blastradius-c8mvz5mw9-phantom3452s-projects.vercel.app";
const OUT = path.resolve("out");
const W = 1600;
const H = 1000;

// Beat durations in ms, summing to 172s (2:52) to match the measured script.
const T = {
  problem: 33_000, // 0:00-0:33  narration over a still console
  project: 14_000, // 0:33-0:47  "This is blastradius..."
  verdict: 20_000, // 0:47-1:07  the numbers
  graph: 23_000, // 1:07-1:30  hover to light the path
  slider: 28_000, // 1:30-1:58  drag through time
  // The first cut gave this 14s and the trace was still in flight at 132s, leaving barely
  // three seconds on the payoff. The verdict flipping to red is the climax of the demo, so
  // the beat is longer and front-loads the interaction to hold the result on screen.
  lockfile: 19_000, // 1:58-2:17  drop the lockfile
  hydra: 35_000, // 2:17-2:52  semantic search
};

const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

/**
 * A beat clock.
 *
 * The first cut ran 189.6s against a 172s script, because setup waits and scroll animations
 * stacked on top of each beat's budget instead of counting against it. Every beat now ends at
 * a fixed offset from the marked start, so the footage lines up with the narration no matter
 * how long the page takes to settle inside it.
 */
class Clock {
  constructor() {
    this.t0 = Date.now();
    this.cursor = 0;
  }
  elapsed() {
    return Date.now() - this.t0;
  }
  /** Hold until `ms` of footage have elapsed since this beat's budget began. */
  async until(ms, label) {
    this.cursor += ms;
    const remain = this.cursor - this.elapsed();
    if (remain < -500) {
      console.warn(`  ! beat "${label}" overran by ${Math.round(-remain)}ms`);
    }
    await sleep(remain);
    console.log(`  ${label} ends at ${(this.elapsed() / 1000).toFixed(1)}s`);
  }
}

/** Smooth scroll so the footage never jump-cuts between sections. */
async function glideTo(page, selector, ms = 1400) {
  await page.evaluate(
    ([sel, dur]) => {
      const el = document.querySelector(sel);
      if (!el) return;
      const target = window.scrollY + el.getBoundingClientRect().top - 90;
      const start = window.scrollY;
      const t0 = performance.now();
      const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
      function step(now) {
        const p = Math.min(1, (now - t0) / dur);
        window.scrollTo(0, start + (target - start) * ease(p));
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    },
    [selector, ms],
  );
  await sleep(ms + 250);
}

/** A visible cursor. The real pointer is not captured in the video stream. */
async function installCursor(page) {
  await page.addStyleTag({
    content: `
      #demo-cursor{position:fixed;width:16px;height:16px;border-radius:50%;
        background:rgba(200,16,46,.85);border:2px solid #fff;box-shadow:0 0 0 1px #c8102e;
        z-index:2147483647;pointer-events:none;transform:translate(-50%,-50%);
        transition:opacity .2s;opacity:0}
    `,
  });
  await page.evaluate(() => {
    const d = document.createElement("div");
    d.id = "demo-cursor";
    document.body.appendChild(d);
    window.__cur = (x, y, show = true) => {
      d.style.left = x + "px";
      d.style.top = y + "px";
      d.style.opacity = show ? "1" : "0";
    };
  });
}

/** Move the drawn cursor and the real pointer together, in an eased path. */
async function glideCursor(page, to, ms = 900) {
  const from = (await page.evaluate(() => window.__lastCur)) ?? { x: W / 2, y: H / 2 };
  const steps = Math.max(12, Math.round(ms / 16));
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps;
    const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    const x = from.x + (to.x - from.x) * e;
    const y = from.y + (to.y - from.y) * e;
    await page.evaluate(([px, py]) => {
      window.__cur(px, py);
      window.__lastCur = { x: px, y: py };
    }, [x, y]);
    await page.mouse.move(x, y);
    await sleep(ms / steps);
  }
}

async function main() {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch({ args: ["--force-color-profile=srgb"] });
  const ctx = await browser.newContext({
    viewport: { width: W, height: H },
    deviceScaleFactor: 2, // retina-sharp text; Computer Modern is thin at 1x
    recordVideo: { dir: OUT, size: { width: W, height: H } },
  });
  const page = await ctx.newPage();

  console.log("loading", URL_);
  await page.goto(URL_, { waitUntil: "networkidle", timeout: 90_000 });
  await page.waitForSelector(".verdict", { timeout: 60_000 });
  await page.waitForFunction(
    () => document.fonts.status === "loaded" || document.fonts.ready,
    null,
    { timeout: 20_000 },
  );
  await installCursor(page);
  await page.evaluate(() => {
    window.__lastCur = { x: 800, y: 500 };
  });
  await sleep(1200);

  const clock = new Clock();

  /* ---------------- 0:00-0:33  problem ---------------- */
  console.log("beat: problem");
  await page.evaluate(() => window.scrollTo(0, 0));
  await clock.until(T.problem, "problem");

  /* ---------------- 0:33-0:47  project ---------------- */
  console.log("beat: project");
  await glideCursor(page, { x: 420, y: 250 }, 900);
  await clock.until(T.project, "project");

  /* ---------------- 0:47-1:07  verdict ---------------- */
  console.log("beat: verdict");
  await glideTo(page, ".verdict", 1300);
  // Trace across the numbers so the eye follows the narration.
  await glideCursor(page, { x: 300, y: 430 }, 1100);
  await sleep(3000);
  await glideCursor(page, { x: 640, y: 430 }, 1100);
  await sleep(3000);
  await glideCursor(page, { x: 980, y: 430 }, 1100);
  await clock.until(T.verdict, "verdict");

  /* ---------------- 1:07-1:33  the graph ---------------- */
  console.log("beat: graph");
  await glideTo(page, ".graph-frame", 1500);
  const nodes = await page.evaluate(() => {
    const svg = document.querySelector(".graph-frame svg");
    return [...svg.querySelectorAll("circle")]
      .filter((c) => c.getAttribute("fill") === "#ffffff" || c.getAttribute("fill") === "#c8102e")
      .map((c) => {
        const b = c.getBoundingClientRect();
        return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2), r: b.width };
      })
      .filter((n) => n.y > 60 && n.y < window.innerHeight - 60);
  });
  // Hover three outer nodes in turn; each lights its own chain back to the centre.
  const picks = nodes.filter((n) => n.r < 30).slice(0, 3);
  const per = (T.graph - 2200) / Math.max(1, picks.length);
  for (const n of picks) {
    await glideCursor(page, { x: n.x, y: n.y }, 850);
    await page.mouse.move(n.x, n.y);
    await sleep(per - 850);
  }
  await clock.until(T.graph, "graph");

  /* ---------------- 1:33-2:03  the scrubber ---------------- */
  console.log("beat: slider");
  await glideTo(page, ".scrubber", 1400);
  const sl = await page.evaluate(() => {
    const el = document.querySelector("input.scrub");
    const b = el.getBoundingClientRect();
    return { x: Math.round(b.x), y: Math.round(b.y + b.height / 2), w: Math.round(b.width) };
  });
  await glideCursor(page, { x: sl.x + 6, y: sl.y }, 900);
  await page.mouse.down();
  // Drag the whole window slowly so the banner flips clean -> VULNERABLE -> clean on camera.
  const dragMs = T.slider - 1400 - 900 - 2600;
  const steps = 80;
  for (let i = 1; i <= steps; i += 1) {
    const x = sl.x + (sl.w - 12) * (i / steps) + 6;
    await page.evaluate(([px, py]) => {
      window.__cur(px, py);
      window.__lastCur = { x: px, y: py };
    }, [x, sl.y]);
    await page.mouse.move(x, sl.y);
    await sleep(dragMs / steps);
  }
  await page.mouse.up();
  await clock.until(T.slider, "slider");

  /* ---------------- 2:03-2:17  the lockfile ---------------- */
  console.log("beat: lockfile");
  await glideTo(page, ".dropfile", 1200);
  const lockPath = path.resolve("../examples/acme-checkout.package-lock.json");
  await page.setInputFiles(".dropfile input[type=file]", lockPath);
  await sleep(1200);
  const trace = await page.evaluate(() => {
    const b = [...document.querySelectorAll("button.press")]
      .find((x) => /trace/i.test(x.textContent))
      .getBoundingClientRect();
    return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) };
  });
  await glideCursor(page, trace, 800);
  await page.mouse.click(trace.x, trace.y);
  await page.waitForTimeout(1200);
  await glideTo(page, ".verdict", 1100);
  await clock.until(T.lockfile, "lockfile");

  /* ---------------- 2:17-2:52  HydraDB ---------------- */
  console.log("beat: hydra");
  const semBtn = await page.evaluate(() => {
    const b = [...document.querySelectorAll("button.press")].find((x) =>
      /search advisories/i.test(x.textContent),
    );
    b.scrollIntoView({ block: "center" });
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });
  await sleep(900);
  const semBtn2 = await page.evaluate(() => {
    const b = [...document.querySelectorAll("button.press")].find((x) =>
      /search advisories/i.test(x.textContent),
    );
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });
  await glideCursor(page, semBtn2, 900);
  await page.mouse.click(semBtn2.x, semBtn2.y);
  await page.waitForSelector(".chunk", { timeout: 30_000 });
  await sleep(2200);
  // Ease down the result list so every hit is readable.
  await page.evaluate(() => {
    const el = document.querySelectorAll(".chunk")[0];
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  });
  await sleep(Math.max(0, T.hydra - 14_000));
  await page.evaluate(() => window.scrollBy({ top: 240, behavior: "smooth" }));
  await clock.until(T.hydra, "hydra");

  console.log(`total footage: ${(clock.elapsed() / 1000).toFixed(1)}s (target 172.0s)`);
  console.log("closing");
  await ctx.close();
  await browser.close();

  const raw = fs.readdirSync(OUT).find((f) => f.endsWith(".webm"));
  console.log("raw video:", path.join(OUT, raw));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
