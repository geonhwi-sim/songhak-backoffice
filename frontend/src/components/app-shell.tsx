"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { Flame, LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, fetcher, type Me } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "홈" },
  { href: "/settlements", label: "정산" },
  { href: "/payroll", label: "인건비" },
  { href: "/ask", label: "질문하기" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: me } = useSWR<Me>("/api/auth/me", fetcher);

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => null);
    // 화면에 남은 데이터(SWR 캐시)까지 지우려고 일부러 새로고침으로 이동한다
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="sticky top-0 z-10 border-b bg-background/90 backdrop-blur print:hidden">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-2 px-4">
          <Link href="/" className="mr-3 flex items-center gap-2 font-bold">
            <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Flame className="size-4" />
            </span>
            <span className="hidden sm:inline">송학구이</span>
          </Link>
          <nav className="flex gap-1 overflow-x-auto">
            {NAV.map((n) => {
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
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
