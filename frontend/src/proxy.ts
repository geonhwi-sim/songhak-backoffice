import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// 쿠키가 없으면 로그인 화면으로 보낸다. 쿠키가 진짜인지는 FastAPI가 요청마다 확인한다 (여기는 빠른 1차 확인).
const COOKIE = "songhak_session";

export function proxy(request: NextRequest) {
  if (!request.cookies.has(COOKIE)) {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // 로그인 화면, API, 정적 파일은 빼고 모든 화면
  matcher: ["/((?!login|api|_next/static|_next/image|favicon.ico).*)"],
};
