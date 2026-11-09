import type { NextConfig } from "next";

// 화면에서 /api/* 로 부르면 FastAPI로 넘긴다. 같은 출처라 로그인 쿠키(HttpOnly)가 그대로 따라간다.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // 운영 이미지(Dockerfile)용. 개발 서버(npm run dev)에는 영향 없음
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
