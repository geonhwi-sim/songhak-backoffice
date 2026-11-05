"use client";

import Link from "next/link";
import useSWR from "swr";
import { RefreshCw, TriangleAlert } from "lucide-react";

import { Empty, ErrorBox, Loading, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetcher, type Home, type SupplyOrder, type SyncRun } from "@/lib/api";
import { hours, kstTime, md, monthLabel, won } from "@/lib/format";

const STATUS: Record<SupplyOrder["status"], { label: string; variant: "default" | "secondary" | "outline" }> = {
  pending: { label: "대기", variant: "default" },
  ordered: { label: "주문함", variant: "secondary" },
  done: { label: "완료", variant: "outline" },
};

export default function HomePage() {
  const { data, error, isLoading, mutate } = useSWR<Home>("/api/home", fetcher);

  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (isLoading || !data) return <Loading rows={6} />;

  const { payroll, settlements, supply_orders: supply } = data;
  const pending = supply.orders.filter((o) => o.status === "pending").length;
  const workDays = payroll.employees.reduce((s, e) => s + e.days, 0);
  const workHours = payroll.employees.reduce((s, e) => s + e.hours, 0);

  return (
    <>
      <PageHeader title="홈" description={`오늘 ${md(data.today)} 기준`} />
      <SyncNotice today={data.today} runs={data.last_sync} />

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          title="이번 급여 인건비"
          sub={`${md(payroll.start)} ~ ${md(payroll.end)}`}
          value={won(payroll.total)}
          foot={`근무 ${workDays}일 · ${hours(workHours)}`}
          href="/payroll"
        />
        <Stat
          title={`${monthLabel(settlements.month)} 정산`}
          sub="정산처 합계"
          value={won(settlements.total)}
          foot={`정산처 ${settlements.places.length}곳`}
          href="/settlements"
        />
        <Stat
          title="발주"
          sub="최근 10건"
          value={`대기 ${pending}건`}
          foot={`최근 비용 ${won(supply.cost)}`}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>정산처별 이번 달 금액</CardTitle>
            <CardDescription>{monthLabel(settlements.month)} 1일부터 오늘까지</CardDescription>
          </CardHeader>
          <CardContent>
            {settlements.places.length === 0 ? (
              <Empty>
                이번 달 정산 기록이 아직 없습니다.{" "}
                <Link href="/settlements" className="font-medium text-primary underline-offset-4 hover:underline">
                  지난달 정산 보기
                </Link>
              </Empty>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>정산처</TableHead>
                    <TableHead className="text-right">주문</TableHead>
                    <TableHead className="text-right">금액</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {settlements.places.map((p) => (
                    <TableRow key={p.place_id}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.orders}건</TableCell>
                      <TableCell className="text-right font-medium tabular-nums">{won(p.amount)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>최근 발주</CardTitle>
            <CardDescription>대기 중인 발주가 먼저 나옵니다</CardDescription>
          </CardHeader>
          <CardContent>
            {supply.orders.length === 0 ? (
              <Empty>발주 기록이 없습니다.</Empty>
            ) : (
              <ul className="divide-y">
                {supply.orders.map((o) => (
                  <li key={o.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <Badge variant={STATUS[o.status].variant}>{STATUS[o.status].label}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">
                        {o.items.map((i) => i.name).join(", ") || "품목 없음"}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {o.requester} · {kstTime(o.created_at)}
                        {o.card_label && ` · ${o.card_label}`}
                      </p>
                    </div>
                    <span className="text-sm font-medium tabular-nums">{won(o.cost)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function Stat(props: { title: string; sub: string; value: string; foot: string; href?: string }) {
  const body = (
    <Card className={props.href ? "transition-colors hover:border-primary/40" : undefined}>
      <CardHeader>
        <CardDescription>
          {props.title} <span className="text-xs">· {props.sub}</span>
        </CardDescription>
        <CardTitle className="text-2xl tabular-nums">{props.value}</CardTitle>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">{props.foot}</CardContent>
    </Card>
  );
  return props.href ? <Link href={props.href}>{body}</Link> : body;
}

/** 숫자가 언제 기준인지 보여 준다. 동기화가 이틀 넘게 없거나 실패했으면 눈에 띄게. */
function SyncNotice({ today, runs }: { today: string; runs: Home["last_sync"] }) {
  const names = { rtdb: "정산 앱", firestore: "발주 앱" } as const;
  const entries = (Object.keys(names) as (keyof typeof names)[]).map((k) => [k, runs[k]] as const);
  const stale = (r?: SyncRun) =>
    !r || r.status !== "succeeded" || (Date.parse(today) - Date.parse(r.started_at)) / 86_400_000 > 2;
  const warn = entries.some(([, r]) => stale(r));

  return (
    <div
      className={`mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-xs ${
        warn ? "border-amber-300 bg-amber-50 text-amber-900" : "text-muted-foreground"
      }`}
    >
      {warn ? <TriangleAlert className="size-3.5" /> : <RefreshCw className="size-3.5" />}
      {entries.map(([k, r]) => (
        <span key={k}>
          {names[k]} 동기화: {r ? `${kstTime(r.started_at)}${r.status === "succeeded" ? "" : " (실패)"}` : "기록 없음"}
        </span>
      ))}
      {warn && <span className="font-medium">숫자가 최신이 아닐 수 있습니다</span>}
    </div>
  );
}
