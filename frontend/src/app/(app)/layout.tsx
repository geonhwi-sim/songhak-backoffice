import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { Loading } from "@/components/common";

// 화면마다 "오늘" 기준 기본 달을 고르므로 미리 만들어 둘 수 없다. 요청 때 그려서 흘려보낸다.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      <Suspense fallback={<Loading rows={5} />}>{children}</Suspense>
    </AppShell>
  );
}
