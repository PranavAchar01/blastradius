"use client";

/**
 * The blast radius, drawn as concentric depth rings — hand-built SVG, no charting library.
 *
 * The layout encodes the answer: the compromised version sits at the centre, and every ring
 * out is one more hop of `DEPENDS_ON`. Each edge drawn is a real traversal step carrying the
 * semver range that made it true, so hovering a node reveals the *path* rather than a tooltip
 * of metadata. The path is the proof — a responder needs to know why they are exposed, not
 * just that a number is large.
 *
 * Red marks packages a lockfile actually resolved. Everything else is black on white.
 */

import { useMemo, useState } from "react";

export interface GraphNode {
  id: string;
  pkg: string;
  version: string;
  depth: number;
  exposed: boolean;
}
export interface GraphEdge {
  from: string;
  to: string;
  range: string | null;
  depType: string;
}

interface Props {
  subject: { pkg: string; version: string };
  nodes: GraphNode[];
  edges: GraphEdge[];
  maxNodes?: number;
}

interface Placed extends GraphNode {
  x: number;
  y: number;
}

const W = 900;
const H = 560;

export default function RadiusGraph({ subject, nodes, edges, maxNodes = 140 }: Props) {
  const [hover, setHover] = useState<string | null>(null);

  const { placed, byId, shown, hidden, rings } = useMemo(() => {
    const centre = `${subject.pkg}@${subject.version}`;
    // Exposed nodes and shallow ones earn their place first; a 700-node ring is unreadable
    // and the truncation is stated in the caption rather than hidden.
    const ranked = [...nodes].sort(
      (a, b) => Number(b.exposed) - Number(a.exposed) || a.depth - b.depth,
    );
    const keep = ranked.slice(0, maxNodes);
    const byDepth = new Map<number, GraphNode[]>();
    for (const n of keep) {
      const list = byDepth.get(n.depth) ?? [];
      list.push(n);
      byDepth.set(n.depth, list);
    }
    const depths = [...byDepth.keys()].sort((a, b) => a - b);
    const maxDepth = depths.length ? Math.max(...depths) : 1;
    const step = Math.min(120, (Math.min(W, H) / 2 - 60) / Math.max(1, maxDepth));

    const out: Placed[] = [
      { id: centre, pkg: subject.pkg, version: subject.version, depth: 0, exposed: true, x: W / 2, y: H / 2 },
    ];
    for (const d of depths) {
      const ring = byDepth.get(d)!;
      const r = step * d + 52;
      // Offset each ring so labels on adjacent rings do not collide.
      const phase = (d % 2) * (Math.PI / ring.length);
      ring.forEach((n, i) => {
        const theta = (i / ring.length) * Math.PI * 2 + phase - Math.PI / 2;
        out.push({ ...n, x: W / 2 + Math.cos(theta) * r, y: H / 2 + Math.sin(theta) * r });
      });
    }
    const map = new Map(out.map((p) => [p.id, p]));
    return {
      placed: out,
      byId: map,
      shown: keep.length,
      hidden: Math.max(0, nodes.length - keep.length),
      rings: depths.map((d) => step * d + 52),
    };
  }, [nodes, subject, maxNodes]);

  const drawn = edges.filter((e) => byId.has(e.from) && byId.has(e.to));

  const hoverChain = useMemo(() => {
    if (!hover) return new Set<string>();
    const chain = new Set<string>([hover]);
    const parent = new Map(edges.map((e) => [e.from, e.to]));
    let cur = hover;
    for (let i = 0; i < 24; i += 1) {
      const nxt = parent.get(cur);
      if (!nxt) break;
      chain.add(nxt);
      cur = nxt;
    }
    return chain;
  }, [hover, edges]);

  const hovered = hover ? byId.get(hover) : null;
  const hoveredEdge = hover ? edges.find((e) => e.from === hover) : null;

  return (
    <div className="graph-frame">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Blast radius by dependency depth">
        {/* depth rings, labelled so the radial axis is readable rather than decorative */}
        {rings.map((r, i) => (
          <g key={`ring-${i}`}>
            <circle
              cx={W / 2}
              cy={H / 2}
              r={r}
              fill="none"
              stroke="#bdbdbd"
              strokeWidth={1}
              strokeDasharray="3 5"
            />
            <text
              x={W / 2}
              y={H / 2 - r - 5}
              textAnchor="middle"
              fontFamily="CMU Typewriter, monospace"
              fontSize={11}
              fill="#9a9a9a"
            >
              depth {i + 1}
            </text>
          </g>
        ))}

        {/* traversal edges — each one a real DEPENDS_ON step */}
        {drawn.map((e, i) => {
          const a = byId.get(e.from)!;
          const b = byId.get(e.to)!;
          const lit = hoverChain.has(e.from) && hoverChain.has(e.to);
          return (
            <line
              key={`e-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={lit ? "#c8102e" : "#000000"}
              strokeWidth={lit ? 2.4 : 1.1}
              opacity={hover ? (lit ? 1 : 0.07) : 0.5}
            />
          );
        })}

        {/* nodes */}
        {placed.map((n) => {
          const isCentre = n.depth === 0;
          const lit = !hover || hoverChain.has(n.id);
          const r = isCentre ? 13 : n.exposed ? 9 : 6.5;
          // Label everything while the closure is small enough to stay legible; past that,
          // only the nodes that carry the verdict keep their names.
          const label = isCentre || n.exposed || hoverChain.has(n.id) || placed.length <= 45;
          return (
            <g
              key={n.id}
              opacity={lit ? 1 : 0.15}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              style={{ cursor: "pointer" }}
            >
              <circle
                cx={n.x}
                cy={n.y}
                r={r}
                fill={isCentre || n.exposed ? "#c8102e" : "#ffffff"}
                stroke={isCentre || n.exposed ? "#c8102e" : "#000000"}
                strokeWidth={2}
              />
              {/* generous invisible hit area — small circles are hard to hover */}
              <circle cx={n.x} cy={n.y} r={16} fill="transparent" />
              {label && (
                <text
                  x={n.x}
                  y={n.y - r - 7}
                  textAnchor="middle"
                  fontFamily="CMU Typewriter, monospace"
                  fontSize={isCentre ? 17 : 12.5}
                  fontWeight={isCentre || n.exposed ? 700 : 400}
                  fill="#000000"
                  // A white halo keeps labels readable where they cross an edge.
                  stroke="#ffffff"
                  strokeWidth={3.5}
                  paintOrder="stroke"
                >
                  {n.pkg}
                </text>
              )}
            </g>
          );
        })}

        {/* hovered path readout — the proof, in words */}
        {hovered && (
          <g>
            <rect x={10} y={H - 52} width={W - 20} height={42} fill="#ffffff" stroke="#000000" />
            <text x={22} y={H - 34} fontFamily="CMU Typewriter, monospace" fontSize={13} fill="#000000">
              {hovered.pkg}@{hovered.version}
              {hoveredEdge ? `  ──DEPENDS_ON(${hoveredEdge.range ?? "*"})──▶  ${hoveredEdge.to}` : "  (compromised package)"}
            </text>
            <text x={22} y={H - 18} fontFamily="CMU Typewriter, monospace" fontSize={11} fill="#6b6b6b">
              depth {hovered.depth}
              {hovered.exposed ? "  ·  resolved by an uploaded lockfile" : ""}
              {hoveredEdge ? `  ·  ${hoveredEdge.depType} dependency` : ""}
            </text>
          </g>
        )}
      </svg>

      <div className="legend">
        <i>
          <span className="swatch red" /> compromised / resolved by your lockfile
        </i>
        <i>
          <span className="swatch hollow" /> transitively affected
        </i>
        <i>hover a node to light its traversal path back to the source</i>
        <i style={{ marginLeft: "auto" }}>
          {shown} of {nodes.length + 1} drawn
          {hidden > 0 ? ` · ${hidden} omitted from the diagram (all counted in the table)` : ""}
        </i>
      </div>
    </div>
  );
}
