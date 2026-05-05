import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: [],
  experimental: {},
  // Exclude Python venvs from Turbopack file traversal (broken symlinks crash the build)
  outputFileTracingExcludes: {
    '*': [
      'python-service/venv310/**',
      'python-service/venv313/**',
      'venv310/**',
      'venv313/**',
    ],
  },
  turbopack: {
    root: __dirname,
    resolveAlias: {
      tailwindcss: path.resolve(__dirname, "node_modules/tailwindcss"),
    },
  },
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      tailwindcss: path.resolve(__dirname, "node_modules/tailwindcss"),
    };
    return config;
  },
};

export default nextConfig;
