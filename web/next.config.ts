import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * The graph is opened at runtime by path, so Next's dependency tracer cannot see it and
   * would ship a function without its database. Naming it here puts the 75MB artifact inside
   * the serverless bundle. Fluid Compute allows up to 5GB, so this is comfortable.
   */
  outputFileTracingIncludes: {
    "/api/**/*": ["./data/blastradius-web.db"],
  },
};

export default nextConfig;
