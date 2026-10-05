/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === "production";

// Build-time guard: FARM_DATA_SOURCE=fixtures is impossible in production
if (isProd && process.env.FARM_DATA_SOURCE === "fixtures" && process.env.FARM_E2E !== "1") {
  throw new Error(
    'FARM_DATA_SOURCE="fixtures" is not allowed when NODE_ENV=production. Fixtures are for local development only.',
  );
}

const cspDirectives = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isProd ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self' https: wss:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
];

const nextConfig = {
  reactCompiler: true,
  compiler: {
    // Keep console.error and console.warn: they carry real failures (startup guard, render errors).
    removeConsole: isProd ? { exclude: ["error", "warn"] } : false,
  },
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: cspDirectives.join("; "),
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
