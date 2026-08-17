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

/**
 * Beat durations in ms, measured from the *actual* voice-over rather than the script estimate.
 *
 * The recorded read runs 183 wpm against the scripted 150, so the first cut of this video was
 * 33s too long. These numbers come from transcribing the take with whisper.cpp, locating the
 * opening phrase of each section, and remapping those timestamps through the audio edit list.
 * Total is 132.14s, matching voice-edited.wav exactly.
 */
const T = {
  problem: 27_010, // "Almost every app you use..."
  project: 8_950, // "This is blastradius..."
  verdict: 15_040, // "Here it is on a real case..."
  graph: 15_510, // "This picture is the proof..."
  slider: 18_250, // "Now I am dragging through time..."
  lockfile: 12_310, // "Finally I will drop in..."
  hydra: 35_070, // "HydraDB powers the half..."
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
  // Wait for the scroll to actually stop rather than assuming it finished in `ms`.
  // requestAnimationFrame is throttled in headless Chromium, so a fixed sleep can return
  // mid-scroll — which left the fixed-position ring overlay stranded at a stale coordinate.
  await page.waitForFunction(
    () => {
      const y = window.scrollY;
      if (window.__lastY === y) return true;
      window.__lastY = y;
      return false;
    },
    null,
    { polling: 100, timeout: 8000 },
  );
  await sleep(180);
}

/**
 * The animation layer.
 *
 * Built as DOM overlays injected into the live page rather than composited in ffmpeg, so the
 * type, rules and colour are literally the same Computer Modern and the same single red as the
 * product. A post-production overlay would drift from the design system the moment either
 * changed.
 *
 * Red stays reserved for confirmed exposure here too. Titles and labels are black on white,
 * and the only red in the chrome is the wordmark and the underline rule, which is exactly how
 * the console itself uses it.
 */
async function installOverlays(page) {
  await page.addStyleTag({
    content: `
      #ovl{position:fixed;inset:0;z-index:2147483646;pointer-events:none;
        font-family:"CMU Serif",Georgia,serif;color:#000}
      #ovl .card{position:absolute;inset:0;background:#fff;display:flex;
        flex-direction:column;align-items:center;justify-content:center;opacity:0;
        transition:opacity .55s ease}
      #ovl .card.on{opacity:1}
      #ovl .wm{font-size:96px;font-weight:700;letter-spacing:-.02em;margin:0;
        opacity:0;transform:translateY(14px);transition:all .7s cubic-bezier(.2,.7,.2,1)}
      #ovl .wm.on{opacity:1;transform:none}
      #ovl .wm em{font-style:normal;color:#c8102e}
      #ovl .rule{width:0;height:3px;background:#c8102e;margin:26px 0 30px;
        transition:width .8s cubic-bezier(.2,.7,.2,1)}
      #ovl .rule.on{width:340px}
      #ovl .sub{font-size:30px;font-style:italic;color:#3a3a3a;max-width:1020px;
        text-align:center;line-height:1.45;opacity:0;transform:translateY(10px);
        transition:all .7s cubic-bezier(.2,.7,.2,1)}
      #ovl .sub.on{opacity:1;transform:none}
      #ovl .lower{position:absolute;left:54px;bottom:54px;background:#000;color:#fff;
        padding:11px 20px;font-family:"CMU Typewriter",monospace;font-size:19px;
        letter-spacing:.1em;text-transform:uppercase;opacity:0;transform:translateY(12px);
        transition:all .45s cubic-bezier(.2,.7,.2,1)}
      #ovl .lower.on{opacity:1;transform:none}
      #ovl .lower b{color:#c8102e;font-weight:700;margin-right:12px}
    `,
  });
  await page.evaluate(() => {
    const o = document.createElement("div");
    o.id = "ovl";
    o.innerHTML = `
      <div class="card" id="ovl-card">
        <h1 class="wm" id="ovl-wm"></h1>
        <div class="rule" id="ovl-rule"></div>
        <div class="sub" id="ovl-sub"></div>
      </div>
      <div class="lower" id="ovl-lower"><b id="ovl-lower-n"></b><span id="ovl-lower-t"></span></div>`;
    document.body.appendChild(o);

    const $ = (id) => document.getElementById(id);
    window.__ovl = {
      card(wm, sub) {
        if (window.__cur) window.__cur(0, 0, false);
        $("ovl-wm").innerHTML = wm;
        $("ovl-sub").textContent = sub || "";
        $("ovl-card").classList.add("on");
        setTimeout(() => $("ovl-wm").classList.add("on"), 180);
        setTimeout(() => $("ovl-rule").classList.add("on"), 620);
        setTimeout(() => $("ovl-sub").classList.add("on"), 900);
      },
      cardOut() {
        if (window.__lastCur && window.__cur) window.__cur(window.__lastCur.x, window.__lastCur.y, true);
        for (const id of ["ovl-wm", "ovl-rule", "ovl-sub"]) $(id).classList.remove("on");
        $("ovl-card").classList.remove("on");
      },
      label(n, t) {
        $("ovl-lower-n").textContent = n;
        $("ovl-lower-t").textContent = t;
        $("ovl-lower").classList.add("on");
      },
      labelOut() {
        $("ovl-lower").classList.remove("on");
      },
    };
  });
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
  await installOverlays(page);
  await page.evaluate(() => {
    window.__lastCur = { x: 800, y: 500 };
  });
  await sleep(1200);

  const clock = new Clock();

  /* ---------------- problem ----------------
   * 27s of narration with nothing to show yet. A static console for that long is dead air,
   * so the title card holds the frame and the problem is restated on screen while it is
   * spoken. The card lifts just before the narration reaches "This is blastradius".
   */
  console.log("beat: problem");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() =>
    window.__ovl.card(
      "blast<em>radius</em>",
      "A poisoned package is live for minutes. Who installed it?",
    ),
  );
  await clock.until(T.problem - 1400, "problem card");
  await page.evaluate(() => window.__ovl.cardOut());
  await clock.until(1400, "problem out");

  /* ---------------- project ---------------- */
  console.log("beat: project");
  await page.evaluate(() => window.__ovl.label("§1", "the compromise"));
  await glideCursor(page, { x: 420, y: 250 }, 900);
  await clock.until(T.project, "project");

  /* ---------------- 0:47-1:07  verdict ---------------- */
  console.log("beat: verdict");
  await page.evaluate(() => window.__ovl.label("§2", "verdict"));
  await glideTo(page, ".verdict", 1300);
  // The product's own hover interaction is the highlight that matters, so the verdict beat
  // simply holds while the narration names each number.
  await clock.until(T.verdict, "verdict");

  /* ---------------- 1:07-1:33  the graph ---------------- */
  console.log("beat: graph");
  await page.evaluate(() => window.__ovl.label("§3", "the path is the proof"));
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
  await page.evaluate(() => window.__ovl.label("§4", "when was it live"));
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
  await page.evaluate(() => window.__ovl.label("§5", "your exposure"));
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
  await page.evaluate(() => window.__ovl.label("§6", "hydradb · semantic search"));
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
  // End card over the closing two sentences, so the last thing on screen is the claim itself.
  await clock.until(T.hydra - 6200, "hydra body");
  await page.evaluate(() => {
    window.__ovl.labelOut();
    window.__ovl.card(
      "blast<em>radius</em>",
      "The graph tells you who is affected. HydraDB tells you what happened.",
    );
  });
  await clock.until(6200, "end card");

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
