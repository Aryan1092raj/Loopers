import { NextResponse, type NextRequest } from "next/server";

// ngrok's free tier answers browser user agents with an HTML warning page unless this header is present.
// next.config.ts rewrites cannot set request headers, so add it here for every proxied call.
export function middleware(req: NextRequest) {
  const headers = new Headers(req.headers);
  headers.set("ngrok-skip-browser-warning", "1");
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: ["/api/:path*", "/media/:path*"] };
