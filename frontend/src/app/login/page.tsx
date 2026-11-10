"use client";

import { useState } from "react";

import { BrandMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, DEMO_MODE, api, type Me } from "@/lib/api";

export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function signIn(path: string, body?: object) {
    setPending(true);
    setError(null);
    try {
      await api<Me>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      // 다른 사이트 주소로 보내지 않도록, 이 사이트 안의 경로만 받는다
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = next?.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "서버에 연결하지 못했습니다");
      setPending(false);
    }
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    signIn("/api/auth/login", { email: form.get("email"), password: form.get("password") });
  }

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2.5">
            <BrandMark className="size-9" />
            <span className="font-display text-[22px] font-normal tracking-wide">송학구이 백오피스</span>
          </CardTitle>
          <CardDescription>
            {DEMO_MODE
              ? "공개 데모입니다. 가상의 식당 데이터로 모든 화면과 AI 질문을 써 볼 수 있습니다."
              : "사장님 계정으로 로그인하세요."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {DEMO_MODE && (
            <div className="mb-5 grid gap-2">
              <Button size="lg" disabled={pending} onClick={() => signIn("/api/auth/demo")}>
                {pending ? "들어가는 중…" : "데모로 둘러보기"}
              </Button>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              <p className="text-xs text-muted-foreground">비밀번호 없이 들어갑니다. 실제 가게 데이터는 없습니다.</p>
            </div>
          )}
          {!DEMO_MODE && (
          <form onSubmit={onSubmit} className="grid gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="email">이메일</Label>
              <Input id="email" name="email" type="email" autoComplete="username" required maxLength={254} autoFocus />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="password">비밀번호</Label>
              <Input id="password" name="password" type="password" autoComplete="current-password" required maxLength={128} />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" size="lg" disabled={pending}>
              {pending ? "확인 중…" : "로그인"}
            </Button>
          </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
