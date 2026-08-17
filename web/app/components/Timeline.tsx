"use client";

/**
 * The temporal query, made draggable.
 *
 * Every position on this track is a real instant, and the affected set is recomputed for that
 * instant by interval overlap — not by filtering a present-tense answer. That distinction is
 * the point: a service that resolved the bad version at 09:01 and pinned back at 09:05 *was*
 * exposed, and is invisible to any view that only knows "now".
 *
 * Bands are drawn from AFFECTED_BY validity intervals, which are dated from real npm publish
 * times — when the vulnerable version actually shipped, not when the advisory was disclosed.
 */

import { useMemo, useState } from "react";

export interface Window {
  from: string | null;
  to: string | null;
  osvId: string;
  severity: string | null;
}

interface Props {
  windows: Window[];
  /**
   * "Now", supplied by the server with the incident report. Reading the clock during render
   * would be impure and would desync server and client markup on hydration — and an unfixed
   * advisory window has to run to *some* instant, so the server's is the honest one.
   */
  now: number;
  onScrub?: (instant: Date) => void;
}

function fmt(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export default function Timeline({ windows, now, onScrub }: Props) {
  const bounds = useMemo(() => {
    const stamps: number[] = [];
    for (const w of windows) {
      if (w.from) stamps.push(Date.parse(w.from));
      if (w.to) stamps.push(Date.parse(w.to));
    }
    if (!stamps.length) return null;
    const lo = Math.min(...stamps);
    // An unfixed window runs to today; pad a little so the head is draggable.
    const hi = Math.max(...stamps, now);
    const pad = Math.max((hi - lo) * 0.02, 86_400_000);
    return { lo: lo - pad, hi: hi + pad };
  }, [windows, now]);

  /**
   * Open on an instant where the package was actually vulnerable, not at "now".
   *
   * Parking the handle at the right edge shows "no window open", which is both the least
   * interesting instant and a misleading first impression — the whole point is that this
   * version *was* exposed for a period. Default to the midpoint of the earliest window.
   */
  const initialPos = useMemo(() => {
    if (!bounds) return 1000;
    const dated = windows.filter((w) => w.from);
    if (!dated.length) return 1000;
    const first = dated.reduce((a, b) => (Date.parse(a.from!) <= Date.parse(b.from!) ? a : b));
    const from = Date.parse(first.from!);
    const to = first.to ? Date.parse(first.to) : bounds.hi;
    const mid = from + (to - from) / 2;
    return Math.round(((mid - bounds.lo) / (bounds.hi - bounds.lo)) * 1000);
  }, [bounds, windows]);

  const [pos, setPos] = useState<number | null>(null);
  const value = pos ?? initialPos;

  if (!bounds) {
    return (
      <div className="scrubber">
        <p className="empty" style={{ padding: 0 }}>
          No dated advisory windows for this version — nothing to scrub. Validity intervals
          exist only where we hold the publish dates of both the introduced and fixed versions.
        </p>
      </div>
    );
  }

  const instant = new Date(bounds.lo + ((bounds.hi - bounds.lo) * value) / 1000);
  const active = windows.filter((w) => {
    const t = instant.getTime();
    const from = w.from ? Date.parse(w.from) : -Infinity;
    const to = w.to ? Date.parse(w.to) : Infinity;
    return t >= from && t < to;
  });

  const pct = (ms: number) => ((ms - bounds.lo) / (bounds.hi - bounds.lo)) * 100;

  return (
    <div className="scrubber">
      <div className="scrub-readout">
        <span className="scrub-now">{fmt(instant)}</span>
        <span>
          {active.length > 0 ? (
            <span style={{ color: "var(--blood)", fontWeight: 700 }}>
              VULNERABLE — {active.length} advisory window{active.length === 1 ? "" : "s"} open
            </span>
          ) : (
            <span style={{ color: "var(--muted)" }}>no advisory window open at this instant</span>
          )}
        </span>
      </div>

      <input
        className="scrub"
        type="range"
        min={0}
        max={1000}
        value={value}
        aria-label="Scrub exposure window"
        onChange={(e) => {
          const v = Number(e.target.value);
          setPos(v);
          onScrub?.(new Date(bounds.lo + ((bounds.hi - bounds.lo) * v) / 1000));
        }}
      />

      <div className="timeline-track">
        {windows.map((w, i) => {
          const from = w.from ? Date.parse(w.from) : bounds.lo;
          const to = w.to ? Date.parse(w.to) : bounds.hi;
          const left = pct(from);
          const width = Math.max(0.6, pct(to) - left);
          return (
            <div
              key={`${w.osvId}-${i}`}
              className="window-band"
              style={{ left: `${left}%`, width: `${width}%`, top: `${i * 7}px` }}
              title={`${w.osvId}: ${w.from?.slice(0, 10) ?? "?"} → ${w.to?.slice(0, 10) ?? "unfixed"}`}
            />
          );
        })}
        <span className="tick" style={{ left: "0%", transform: "translateX(0)" }}>
          {fmt(new Date(bounds.lo))}
        </span>
        <span className="tick" style={{ left: "100%", transform: "translateX(-100%)" }}>
          {fmt(new Date(bounds.hi))}
        </span>
      </div>

      {active.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: "0.9rem 0 0" }}>
          {active.map((w) => (
            <li key={w.osvId} className="mono" style={{ fontSize: "0.8rem", marginBottom: "0.2rem" }}>
              <span className="flag">{w.severity ?? "UNRATED"}</span>{" "}
              <b>{w.osvId}</b>{" "}
              <span style={{ color: "var(--muted)" }}>
                {w.from?.slice(0, 10)} → {w.to ? w.to.slice(0, 10) : "unfixed"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
