import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  // Without the NEXT_PUBLIC_ prefix Next keeps NEXT_MOCK out of the browser bundle; inline it so lib/api.ts can read it.
  env: { NEXT_MOCK: process.env.NEXT_MOCK ?? "" },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/media/:path*", destination: `${API}/media/:path*` },
    ];
  },
  // The first /api/chat loads the embedding model and reranker; Next's default proxy timeout is 30 s.
  experimental: { proxyTimeout: 120_000 },
};

export default nextConfig;