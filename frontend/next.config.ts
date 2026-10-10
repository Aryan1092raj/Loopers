import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
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