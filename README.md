# blastradius

A supply-chain blast-radius console. When an npm package is compromised, answer the only
questions that matter in the first ten minutes:

- Who transitively depends on the bad version?
- Which version introduced the vulnerability?
- Which of *my* services resolved it **while it was live**?
- What else does that maintainer publish?
- What typosquats sit next to the compromised name?
- What is the full rollup — services exposed, depth, first-exposure time?

Built for Hack Hydra, Track 02A.

> **Status:** in development (started Aug 14 2026). See [STATE.md](STATE.md) for the live
> build log, phase gates, and open blockers. Coverage caps and limitations are documented
> honestly here and in [EVAL.md](EVAL.md) — nothing is silently truncated.

## Layout

| Path      | What                                                       |
| --------- | ---------------------------------------------------------- |
| `ingest/` | Python 3.12 + uv. npm registry + OSV ingest, graph build, eval harness. |
| `web/`    | Next.js App Router + TypeScript (strict) + Tailwind. The console. |

## Setup

Requires Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+, and pnpm.

```bash
cp .env.example .env   # then fill in HYDRADB_API_KEY
```

Provision a HydraDB instance at [dashboard.hydradb.com](https://dashboard.hydradb.com) and put
the key in `.env`. The key is read from the environment only — it is never written to a config
file and never committed.

Full cold-clone setup instructions land in P6.

## License

MIT — see [LICENSE](LICENSE).
