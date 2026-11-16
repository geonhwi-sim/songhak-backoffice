"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { LogOut } from "lucide-react";

import { BrandMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { DEMO_MODE, api, fetcher, type Me } from "@/lib/api";
import { cn } from "@/lib/utils";

const OWNER_NAV = [
  { href: "/", label: "홈" },
  { href: "/settlements", label: "정산" },
  { href: "/payroll", label: "인건비" },
  { href: "/supply", label: "발주" },
  { href: "/ask", label: "질문하기" },
  { href: "/sync", label: "동기화" },
];
const STAFF_NAV = [{ href: "/my", label: "내 근무" }];
const STAFF_HOME = "/my";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: me, error: meError } = useSWR<Me>("/api/auth/me", fetcher);
  const staff = me?.role === "staff";
  // 메뉴는 편의일 뿐이고 실제로 막는 것은 API 권한(403)이다. 직원이 사장님 화면 주소로 들어오면 내 근무로 보낸다
  const wrongPage = me != null && (staff ? !pathname.startsWith(STAFF_HOME) : pathname.startsWith(STAFF_HOME));
  useEffect(() => {
    if (wrongPage) router.replace(staff ? STAFF_HOME : "/");
  }, [wrongPage, staff, router]);
  const nav = me == null ? [] : staff ? STAFF_NAV : OWNER_NAV;

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => null);
    // 화면에 남은 데이터(SWR 캐시)까지 지우려고 일부러 새로고침으로 이동한다
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }

  return (
    <div className="flex flex-1 flex-col">
      {DEMO_MODE && (
        <div className="bg-foreground px-4 py-1.5 text-center text-xs text-background print:hidden">
          공개 데모 · 숫자와 이름은 모두 가상 데이터입니다
        </div>
      )}
      <header className="sticky top-0 z-10 border-b bg-background/90 backdrop-blur print:hidden">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-2 px-4">
          <Link href={staff ? STAFF_HOME : "/"} className="mr-3 flex items-center gap-2">
            <BrandMark />
            <span className="hidden font-display text-xl tracking-wide sm:inline">송학구이 백오피스</span>
          </Link>
          <nav className="flex gap-1 overflow-x-auto">
            {nav.map((n) => {
              const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                    active && "bg-muted font-medium text-foreground",
                  )}
                >
                  {n.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground md:inline">{me?.email}</span>
            <Button variant="ghost" size="sm" onClick={logout} aria-label="로그아웃">
              <LogOut />
              <span className="hidden sm:inline">로그아웃</span>
            </Button>
          </div>
        </div>
      </header>
      {/* 역할을 알기 전이나 다른 역할의 화면이면 내용을 그리지 않는다 (403 오류가 잠깐 보이지 않게) */}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{(me == null && !meError) || wrongPage ? null : children}</main>
    </div>
  );
}
