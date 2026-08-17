import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * The graph is opened at runtime by path, so Next's dependency tracer cannot see it and
   * would otherwise ship a function without its database — which fails as
   * `ERR_SQLITE_ERROR: unable to open database file`, only in production.
   *
   * The key is matched against route paths, so it is spelled out per route rather than with
   * an `/api/**` glob, which silently matched nothing. Fluid Compute allows 5GB bundles, so
   * the 75MB artifact is comfortable.
   */
  outputFileTracingIncludes: {
    "/api/incident": ["./data/blastradius-web.db"],
    "/api/search": ["./data/blastradius-web.db"],
  },
};

export default nextConfig;
