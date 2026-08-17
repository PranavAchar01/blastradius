"use client";

/**
 * The console. Every element here is hand-built — no component library, no UI kit.
 *
 * The layout follows the order a responder actually asks things in: what is it, how far does
 * it reach, is *my* service in it, when was it live, and what else should I look at. Latency
 * is shown on every panel because the point being demonstrated is that exact graph traversal
 * is fast, not merely possible.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import RadiusGraph, { type GraphEdge, type GraphNode } from "./components/RadiusGraph";
import Timeline, { type Window as ExposureWindow } from "./components/Timeline";

interface Hop {
  pkg: string;
  version: string;
  depth: number;
  via: string;
}
interface PathEdge {
  from: string;
  to: string;
  range: string | null;
  depType: string;
  depth: number;
}
interface Advisory {
  osvId: string;
  severity: string | null;
  summary: string | null;
  introduced: string;
  fixed: string | null;
  validFrom: string | null;
  validTo: string | null;
  publishedAt: string;
}
interface ExposedService {
  serviceName: string;
  viaPackage: string;
  viaVersion: string;
  depth: number;
  chain: string[];
}
interface Incident {
  subject: { pkg: string; version: string };
  closure: Hop[];
  path: PathEdge[];
  advisories: Advisory[];
  exposedServices: ExposedService[];
  siblings: { pkg: string; handle: string; rank: number | null }[];
  squats: { candidate: string; distance: number; flaggedMalicious: boolean }[];
  timings: Record<string, number>;
  notes: string[];
  timeline: ExposureWindow[];
  package: { name: string; downloads: number | null; rank: number | null; versions: string[]; latest: string | null };
  lockfile: { serviceName: string; version: number | null; entries: number } | null;
  totalMs: number;
  generatedAt: string;
}

const DEMO = { pkg: "lodash", version: "3.10.1" };

function ms(v: number | undefined): string {
  if (v === undefined) return "—";
  return v < 1 ? `${v.toFixed(3)} ms` : `${v.toFixed(1)} ms`;
}

export default function Console() {
  const [pkg, setPkg] = useState(DEMO.pkg);
  const [version, setVersion] = useState(DEMO.version);
  const [lockfile, setLockfile] = useState<{ name: string; json: unknown } | null>(null);
  const [data, setData] = useState<Incident | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [names, setNames] = useState<string[]>([]);

  const [semanticQ, setSemanticQ] = useState(
    "malicious postinstall script that steals environment variables",
  );
  const [semantic, setSemantic] = useState<{
    chunks: { id: string; score: number; text: string }[];
    serverMs: number | null;
    wallMs: number;
  } | null>(null);
  const [semanticBusy, setSemanticBusy] = useState(false);
  const [semanticErr, setSemanticErr] = useState<string | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/incident", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pkg, version: version || undefined, lockfile: lockfile?.json }),
      });
      const body = await res.json();
      if (!res.ok) {
        setError(body.hint ? `${body.error} — ${body.hint}` : body.error);
        setData(null);
      } else {
        setData(body as Incident);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [pkg, version, lockfile]);

  // Run the demo case on first paint so the console is never an empty box. Deferred out of
  // the effect body so the initial state update is not synchronous with mount.
  useEffect(() => {
    const id = setTimeout(() => void run(), 0);
    return () => clearTimeout(id);
    // Intentionally mount-only: later runs are user-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced typeahead. Every state update happens inside the timer, never in the effect body.
  useEffect(() => {
    const id = setTimeout(async () => {
      if (pkg.length < 2) {
        setNames([]);
        return;
      }
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(pkg)}`);
        const b = await r.json();
        setNames((b.names ?? []).filter((n: string) => n !== pkg));
      } catch {
        setNames([]);
      }
    }, 140);
    return () => clearTimeout(id);
  }, [pkg]);

  const askSemantic = useCallback(async () => {
    setSemanticBusy(true);
    setSemanticErr(null);
    try {
      const r = await fetch("/api/semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: semanticQ }),
      });
      const b = await r.json();
      if (!r.ok) setSemanticErr(b.error ?? `HTTP ${r.status}`);
      else setSemantic(b);
    } catch (err) {
      setSemanticErr((err as Error).message);
    } finally {
      setSemanticBusy(false);
    }
  }, [semanticQ]);

  const takeFile = useCallback(async (file: File) => {
    try {
      const text = await file.text();
      setLockfile({ name: file.name, json: JSON.parse(text) });
    } catch {
      setError(`${file.name} is not valid JSON`);
    }
  }, []);

  const exposedKeys = new Set(
    (data?.exposedServices ?? []).map((s) => `${s.viaPackage}@${s.viaVersion}`),
  );
  const nodes: GraphNode[] = (data?.closure ?? []).map((h) => ({
    id: `${h.pkg}@${h.version}`,
    pkg: h.pkg,
    version: h.version,
    depth: h.depth,
    exposed: exposedKeys.has(`${h.pkg}@${h.version}`),
  }));
  const edges: GraphEdge[] = (data?.path ?? []).map((p) => ({
    from: p.from,
    to: p.to,
    range: p.range,
    depType: p.depType,
  }));

  const exposed = (data?.exposedServices.length ?? 0) > 0;
  const firstExposure = data?.timeline?.[0]?.from ?? null;

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1 className="wordmark">
            blast<em>radius</em>
          </h1>
          <p className="tagline">
            Who is exposed, how deep, and since when — answered by traversal, not by similarity.
          </p>
        </div>
        <div className="masthead-meta">
          25,000 packages · 226,832 advisories
          <br />
          Hack Hydra · Track 02A
          {data && (
            <>
              <br />
              <span style={{ color: "var(--blood)" }}>
                total {ms(data.totalMs)}
              </span>
            </>
          )}
        </div>
      </header>

      {/* ------------------------------------------------ query */}
      <section>
        <h2 className="section-title">
          <span className="n">§1</span> The compromise
          <span className="hint">paste a package@version, or drop in your package-lock.json</span>
        </h2>

        <div className="query-bar">
          <div style={{ display: "grid", gap: "0.55rem" }}>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: "0.55rem" }}>
              <div className="field">
                <label htmlFor="pkg">package</label>
                <input
                  id="pkg"
                  value={pkg}
                  spellCheck={false}
                  autoComplete="off"
                  onChange={(e) => setPkg(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void run()}
                  placeholder="lodash"
                />
              </div>
              <div className="field">
                <label htmlFor="ver">version</label>
                <input
                  id="ver"
                  value={version}
                  spellCheck={false}
                  autoComplete="off"
                  onChange={(e) => setVersion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void run()}
                  placeholder="latest"
                />
              </div>
            </div>

            <label
              className="dropfile"
              data-drag={dragging}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const f = e.dataTransfer.files?.[0];
                if (f) void takeFile(f);
              }}
              onClick={() => fileRef.current?.click()}
            >
              <span>
                {lockfile
                  ? `▣ ${lockfile.name} — ${data?.lockfile?.entries ?? "?"} resolved entries`
                  : "▢ drop package-lock.json here to make “your exposure” real"}
              </span>
              {lockfile && (
                <button
                  className="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    setLockfile(null);
                  }}
                >
                  clear
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept=".json,application/json"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void takeFile(f);
                }}
              />
            </label>
          </div>

          <button className="press" onClick={() => void run()} disabled={busy}>
            {busy ? "tracing…" : "trace blast radius"}
          </button>
        </div>

        {names.length > 0 && (
          <ul className="suggestions">
            {names.map((n) => (
              <li key={n}>
                <button onClick={() => setPkg(n)}>{n}</button>
              </li>
            ))}
          </ul>
        )}

        {data && data.package.versions.length > 1 && (
          <ul className="suggestions" style={{ marginTop: "0.5rem" }}>
            <li className="latency" style={{ alignSelf: "center", marginRight: "0.3rem" }}>
              known versions:
            </li>
            {data.package.versions.slice(0, 12).map((v) => (
              <li key={v}>
                <button
                  onClick={() => {
                    setVersion(v);
                  }}
                  style={v === data.subject.version ? { borderColor: "var(--blood)", color: "var(--blood)" } : undefined}
                >
                  {v}
                </button>
              </li>
            ))}
          </ul>
        )}

        {error && <p className="note warn">{error}</p>}
      </section>

      {data && (
        <>
          {/* --------------------------------------------- verdict */}
          <hr className="rule" />
          <section>
            <h2 className="section-title">
              <span className="n">§2</span> Verdict
              <span className="hint">every number below is a traversal, timed</span>
            </h2>

            <div className="verdict" data-exposed={exposed}>
              <div className="stat">
                <div className="k">subject</div>
                <div className="v" style={{ fontSize: "1.15rem", fontFamily: "var(--mono)" }}>
                  {data.subject.pkg}
                </div>
                <div className="sub mono">@{data.subject.version}</div>
              </div>
              <div className="stat">
                <div className="k">blast radius</div>
                <div className={`v ${data.closure.length ? "red" : ""}`}>{data.closure.length}</div>
                <div className="sub">package-versions · {ms(data.timings.blastRadius)}</div>
              </div>
              <div className="stat">
                <div className="k">max depth</div>
                <div className="v">{data.closure.reduce((m, h) => Math.max(m, h.depth), 0)}</div>
                <div className="sub">hops from source</div>
              </div>
              <div className="stat">
                <div className="k">your services</div>
                <div className={`v ${exposed ? "red" : ""}`}>{data.exposedServices.length}</div>
                <div className="sub">{data.lockfile ? "from your lockfile" : "no lockfile uploaded"}</div>
              </div>
              <div className="stat">
                <div className="k">advisories</div>
                <div className={`v ${data.advisories.length ? "red" : ""}`}>{data.advisories.length}</div>
                <div className="sub">covering this version</div>
              </div>
              <div className="stat">
                <div className="k">first exposure</div>
                <div className="v" style={{ fontSize: "1.15rem" }}>
                  {firstExposure ? firstExposure.slice(0, 10) : "—"}
                </div>
                <div className="sub">when the bad version shipped</div>
              </div>
            </div>

            {data.notes.map((n) => (
              <p className="note warn" key={n}>
                ⚠ {n}
              </p>
            ))}
          </section>

          {/* --------------------------------------------- graph */}
          <hr className="rule" />
          <section>
            <h2 className="section-title">
              <span className="n">§3</span> The path is the proof
              <span className="hint">
                each ring is one DEPENDS_ON hop — hover any node to light its chain
              </span>
            </h2>
            {nodes.length ? (
              <RadiusGraph subject={data.subject} nodes={nodes} edges={edges} />
            ) : (
              <p className="empty">
                Nothing depends on this exact version inside the coverage cap. That is an
                answer, not an error — try a version with known dependents.
              </p>
            )}
          </section>

          {/* --------------------------------------------- temporal */}
          <hr className="rule" />
          <section>
            <h2 className="section-title">
              <span className="n">§4</span> When was it live?
              <span className="hint">
                drag — the affected set is recomputed by interval overlap, not post-filtered
              </span>
            </h2>
            <Timeline windows={data.timeline} now={Date.parse(data.generatedAt)} />
          </section>

          {/* --------------------------------------------- tables */}
          <hr className="rule" />
          <div className="grid-2">
            <section>
              <h2 className="section-title">
                <span className="n">§5</span> Exposed services
                <span className="latency" style={{ marginLeft: "auto" }}>
                  <b>{ms(data.timings.blastRadius)}</b>
                </span>
              </h2>
              {data.exposedServices.length ? (
                <div className="scroll-y">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>service</th>
                        <th>via</th>
                        <th className="num">depth</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.exposedServices.map((s, i) => (
                        <tr key={`${s.viaPackage}-${i}`}>
                          <td className="pkg">{s.serviceName}</td>
                          <td>
                            <span className="mono">
                              {s.viaPackage}
                              <span className="ver">@{s.viaVersion}</span>
                            </span>
                            {s.chain.length > 1 && (
                              <div className="latency" style={{ marginTop: "0.15rem" }}>
                                {s.chain.join(" → ")}
                              </div>
                            )}
                          </td>
                          <td className="num">
                            <span className="depth-chip">{s.depth}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">
                  {data.lockfile
                    ? `None of ${data.lockfile.entries} entries in ${data.lockfile.serviceName} resolve into this blast radius.`
                    : "Upload a package-lock.json above to evaluate your own exposure."}
                </p>
              )}
            </section>

            <section>
              <h2 className="section-title">
                <span className="n">§6</span> Advisories
                <span className="latency" style={{ marginLeft: "auto" }}>
                  <b>{ms(data.timings.advisories)}</b>
                </span>
              </h2>
              {data.advisories.length ? (
                <div className="scroll-y">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>id</th>
                        <th>window</th>
                        <th>severity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.advisories.map((a) => (
                        <tr key={`${a.osvId}-${a.introduced}`}>
                          <td className="mono" style={{ fontWeight: 700 }}>
                            {a.osvId}
                            {a.summary && (
                              <div className="latency" style={{ marginTop: "0.15rem", whiteSpace: "normal" }}>
                                {a.summary.slice(0, 90)}
                              </div>
                            )}
                          </td>
                          <td className="mono" style={{ fontSize: "0.8rem" }}>
                            {a.introduced} → {a.fixed ?? "unfixed"}
                          </td>
                          <td>
                            <span className="flag">{a.severity ?? "UNRATED"}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">No advisory covers this version.</p>
              )}
            </section>

            <section>
              <h2 className="section-title">
                <span className="n">§7</span> Same maintainer
                <span className="latency" style={{ marginLeft: "auto" }}>
                  <b>{ms(data.timings.maintainers)}</b>
                </span>
              </h2>
              {data.siblings.length ? (
                <div className="scroll-y">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>package</th>
                        <th>maintainer</th>
                        <th className="num">rank</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.siblings.map((s) => (
                        <tr key={`${s.pkg}-${s.handle}`}>
                          <td className="mono">{s.pkg}</td>
                          <td className="mono" style={{ color: "var(--muted)" }}>
                            {s.handle}
                          </td>
                          <td className="num">{s.rank ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">No co-maintained packages inside the cap.</p>
              )}
            </section>

            <section>
              <h2 className="section-title">
                <span className="n">§8</span> Typosquats
                <span className="latency" style={{ marginLeft: "auto" }}>
                  <b>{ms(data.timings.typosquats)}</b>
                </span>
              </h2>
              {data.squats.length ? (
                <div className="scroll-y">
                  <table className="ledger">
                    <thead>
                      <tr>
                        <th>candidate</th>
                        <th className="num">edits</th>
                        <th>osv</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.squats.map((s) => (
                        <tr key={s.candidate}>
                          <td className="mono" style={s.flaggedMalicious ? { color: "var(--blood)", fontWeight: 700 } : undefined}>
                            {s.candidate}
                          </td>
                          <td className="num">{s.distance}</td>
                          <td>
                            {s.flaggedMalicious ? (
                              <span className="flag">MALICIOUS</span>
                            ) : (
                              <span className="flag ink">unflagged</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="empty">No names within edit distance 2.</p>
              )}
              <p className="note">
                Orthographic distance, not semantic — <code>1odash</code> and <code>lodash</code>{" "}
                are one keystroke apart and identical to an embedding model, which is exactly why
                the attack works.
              </p>
            </section>
          </div>

          {/* --------------------------------------------- hydra */}
          <hr className="rule" />
          <section>
            <h2 className="section-title">
              <span className="n">§9</span> Ask the corpus
              <span className="hint">
                HydraDB — the half the graph cannot do: meaning, not reachability
              </span>
            </h2>
            <div className="query-bar">
              <div className="field">
                <label htmlFor="sem">describe it</label>
                <input
                  id="sem"
                  value={semanticQ}
                  onChange={(e) => setSemanticQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void askSemantic()}
                  placeholder="prototype pollution in a deep merge utility"
                />
              </div>
              <button className="press" onClick={() => void askSemantic()} disabled={semanticBusy}>
                {semanticBusy ? "asking…" : "search advisories"}
              </button>
            </div>

            {semanticErr && <p className="note warn">{semanticErr}</p>}

            {semantic && (
              <>
                <p className="latency" style={{ margin: "0.7rem 0 0.6rem" }}>
                  {semantic.chunks.length} passages · server <b>{semantic.serverMs?.toFixed(0) ?? "—"} ms</b> · round trip{" "}
                  <b>{semantic.wallMs.toFixed(0)} ms</b>
                </p>
                {semantic.chunks.map((c) => (
                  <div className="chunk" key={c.id}>
                    <span className="cid">{c.id.replace(/^adv:/, "")}</span>{" "}
                    <span className="score">{c.score.toFixed(3)}</span>
                    <p>{c.text}</p>
                  </div>
                ))}
                <p className="note">
                  None of those query words appear in the package names retrieved — no SQL
                  predicate over the same corpus finds them. That is why both halves exist.
                </p>
              </>
            )}
          </section>
        </>
      )}

      <footer className="colophon">
        <span>
          Coverage: top 25,000 npm packages by weekly downloads · complete OSV advisory corpus ·
          advisories after 2026-01-01 held out of the graph entirely.
        </span>
        <span>
          Set in Computer Modern ·{" "}
          <a href="https://github.com/pranavachar/blastradius">source &amp; method</a>
        </span>
      </footer>
    </div>
  );
}
