/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The Python API is the same origin in production (see vercel.json), so the
  // browser calls /api/... and /auth/... with no base URL and no CORS. In dev
  // the FastAPI server runs separately, so proxy those prefixes to it — the
  // point is that the front-end code never knows the difference.
  async rewrites() {
    const api = process.env.PATHIA_API_ORIGIN;
    if (!api) return [];
    return [
      { source: '/api/:path*', destination: `${api}/api/:path*` },
      { source: '/auth/:path*', destination: `${api}/auth/:path*` },
      { source: '/demo/:path*', destination: `${api}/demo/:path*` },
      { source: '/static/:path*', destination: `${api}/static/:path*` },
    ];
  },
};
export default nextConfig;
