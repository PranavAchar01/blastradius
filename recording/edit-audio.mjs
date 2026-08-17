/**
 * Builds the audio edit decision list and renders the cut voice track.
 *
 * Cuts fall into three kinds:
 *
 *  1. Stutters. Two were found by transcribing with whisper.cpp and reading the result
 *     against the script. Both are removed.
 *  2. Head and tail silence.
 *  3. Long pauses, tightened to TIGHT_TO rather than removed, so the delivery keeps its
 *     breath and the animations still have room to land.
 *
 * Every cut boundary is placed inside a measured silence region wherever possible, because a
 * join between two silent samples is inaudible while a join mid-syllable clicks. The one
 * exception is documented at its cut.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";

const SRC = "voice-mastered.wav";
const OUT = "voice-edited.wav";
const DUR = 140.99;

const TIGHT_TO = 0.36; // a natural beat between sentences
const TIGHTEN_OVER = 0.55; // only pauses longer than this are touched

/** Measured silence regions, from ffmpeg silencedetect at -30dB. */
const silences = fs
  .readFileSync("/tmp/sil.txt", "utf8")
  .trim()
  .split("\n")
  .map((l) => l.trim().split(/\s+/).map(Number))
  .filter((p) => p.length === 2 && !Number.isNaN(p[0]));

/** Regions to delete outright. */
const cuts = [
  // Head silence. Speech starts at 2.70; leave a 120ms lead-in.
  { from: 0, to: 2.58, why: "head silence" },

  // "whether they're just their own product" — "they're just" is a false start.
  // The trailing boundary sits inside the 21.953-22.164 silence, which is clean. The leading
  // boundary is mid-speech after "whether", the only such cut in the edit, so it gets a short
  // fade at render time to avoid a click.
  { from: 21.42, to: 22.10, why: 'false start "they\'re just"', fade: true },

  // A stray "Scroll" — a screen direction read aloud. Bracketed by silence at both ends
  // (102.108-103.064 and 103.582-103.751), so this join is inaudible.
  { from: 102.70, to: 103.68, why: 'stray word "Scroll"' },

  // Tail silence after the final word at 140.52.
  { from: 140.62, to: DUR, why: "tail silence" },
];

// Tighten every long pause that is not already inside a cut.
const overlaps = (a, b) => cuts.some((c) => a < c.to && b > c.from);
for (const [s, e] of silences) {
  const len = e - s;
  if (len <= TIGHTEN_OVER) continue;
  if (overlaps(s, e)) continue;
  // Trim from the middle so both edges keep their natural decay and onset.
  const excess = len - TIGHT_TO;
  const mid = (s + e) / 2;
  cuts.push({ from: mid - excess / 2, to: mid + excess / 2, why: `tighten ${len.toFixed(2)}s pause` });
}

cuts.sort((a, b) => a.from - b.from);

// Invert the cut list into keep-segments.
const keeps = [];
let cursor = 0;
for (const c of cuts) {
  if (c.from > cursor) keeps.push({ from: cursor, to: c.from, fadeIn: false });
  cursor = Math.max(cursor, c.to);
}
if (cursor < DUR) keeps.push({ from: cursor, to: DUR });

const removed = cuts.reduce((n, c) => n + (c.to - c.from), 0);
const finalDur = keeps.reduce((n, k) => n + (k.to - k.from), 0);

console.log("cuts:");
for (const c of cuts) {
  console.log(`  ${c.from.toFixed(2)} -> ${c.to.toFixed(2)}  (${(c.to - c.from).toFixed(2)}s)  ${c.why}`);
}
console.log(`\nsegments kept : ${keeps.length}`);
console.log(`removed       : ${removed.toFixed(2)}s`);
console.log(`final duration: ${finalDur.toFixed(2)}s  (was ${DUR.toFixed(2)}s)`);

// Render. 8ms fades at each join kill any click without being audible as a fade.
const F = 0.008;
const parts = keeps.map((k, i) => {
  const d = k.to - k.from;
  return (
    `[0:a]atrim=start=${k.from.toFixed(4)}:end=${k.to.toFixed(4)},asetpts=PTS-STARTPTS,` +
    `afade=t=in:st=0:d=${F},afade=t=out:st=${Math.max(0, d - F).toFixed(4)}:d=${F}[s${i}]`
  );
});
const filter =
  parts.join(";") +
  ";" +
  keeps.map((_, i) => `[s${i}]`).join("") +
  `concat=n=${keeps.length}:v=0:a=1[out]`;

fs.writeFileSync("/tmp/filter.txt", filter);
execFileSync(
  "ffmpeg",
  ["-hide_banner", "-y", "-i", SRC, "-filter_complex_script", "/tmp/filter.txt", "-map", "[out]", "-c:a", "pcm_s16le", OUT],
  { stdio: ["ignore", "ignore", "pipe"] },
);

const probe = execFileSync("ffprobe", [
  "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", OUT,
]).toString().trim();
console.log(`\nrendered ${OUT}: ${Number(probe).toFixed(2)}s`);
fs.writeFileSync("edl.json", JSON.stringify({ cuts, keeps, finalDur }, null, 2));
