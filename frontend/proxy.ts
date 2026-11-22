import { NextResponse, type NextRequest } from "next/server";

/**
 * Serve the API from this site's own origin (ADR-022). When BACKEND_URL is set (a deployed
 * frontend), `/api/*` and `/auth/*` are forwarded to the backend, so the owner cookie is
 * first-party (browsers that block third-party cookies still keep the user's cases) and no
 * CORS is involved. `/auth/*` keeps its path because the Google sign-in state cookie is scoped
 * to `/auth/google`. Locally, BACKEND_URL is unset and the browser calls the API directly.
 */
const BACKEND = process.env.BACKEND_URL?.replace(/\/+$/, "");

export function proxy(request: NextRequest) {
  if (!BACKEND) return NextResponse.next();
  const { pathname, search } = request.nextUrl;
  const path = pathname.startsWith("/api/") ? pathname.slice("/api".length) : pathname;
  const headers = new Headers(request.headers);
  headers.delete("host"); // the backend's host routes the request, not this site's
  headers.set("ngrok-skip-browser-warning", "1"); // a tunnel's interstitial would replace JSON
  return NextResponse.rewrite(new URL(path + search, BACKEND), { request: { headers } });
}

export const config = {
  matcher: ["/api/:path*", "/auth/:path*"],
};
