"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { ChevronDown } from "lucide-react";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetcher, type SupplyOrder, type SupplyOrders } from "@/lib/api";
import { kstTime, md, monthLabel, num, todayKst, won } from "@/lib/format";
import { SUPPLY_STATUS } from "@/lib/labels";
import { cn } from "@/lib/utils";

type Filter = "all" | SupplyOrder["status"];

const monthEnd = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return `${m}-${String(new Date(y, mo, 0).getDate()).padStart(2, "0")}`;
};

// 발주: 한 달 동안의 발주와 비용. 비용은 결제 금액이 있으면 그 값, 없으면 단가표 기준 예상 금액이다 (backend reports.supply_orders)
export default function SupplyPage() {
  const [month, setMonth] = useState(() => todayKst().slice(0, 7));
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, isLoading, mutate } = useSWR<SupplyOrders>(
    `/api/supply-orders?start=${month}-01&end=${monthEnd(month)}`,
    fetcher,
  );

  const summary = useMemo(() => {
    const orders = data?.orders ?? [];
    const count = { pending: 0, ordered: 0, done: 0 };
    let paid = 0;
    let estimated = 0;
    const byItem = new Map<string, { name: string; unit: string | null; qty: number; amount: number; orders: number }>();
    for (const o of orders) {
      count[o.status] += 1;
      if (o.paid_amount != null) paid += o.paid_amount;
      else estimated += o.estimated_total ?? 0;
      for (const it of o.items) {
        const cur = byItem.get(it.name) ?? { name: it.name, unit: it.unit, qty: 0, amount: 0, orders: 0 };
        cur.qty += it.quantity;
        cur.amount += it.subtotal ?? 0;
        cur.orders += 1;
        byItem.set(it.name, cur);
      }
    }
    // 품목별 금액은 단가표 기준이다 (결제는 발주 단위라 품목별로 나눌 수 없다)
    const items = [...byItem.values()].sort((a, b) => b.amount - a.amount || b.orders - a.orders).slice(0, 10);
    return { count, paid, estimated, items };
  }, [data]);

  const changeMonth = (m: string) => {
    setMonth(m);
    setOpen(null);
  };
  const shown = (data?.orders ?? []).filter((o) => filter === "all" || o.status === filter);

  return (
    <>
      <PageHeader title="발주" description="발주 앱에서 매일 05시에 가져온 기록입니다. 대기 중인 발주가 먼저 나옵니다.">
        <MonthPicker value={month} onChange={changeMonth} />
      </PageHeader>

      {error ? (
        <ErrorBox error={error} onRetry={() => mutate()} />
      ) : isLoading || !data ? (
        <Loading rows={6} />
      ) : data.count === 0 ? (
        <Empty>{monthLabel(month)} 발주 기록이 없습니다.</Empty>
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardDescription>{monthLabel(month)} 발주</CardDescription>
                <CardTitle className="text-2xl tabular-nums">{data.count}건</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-1.5">
                {(Object.keys(SUPPLY_STATUS) as SupplyOrder["status"][]).map((s) => (
                  <Badge key={s} variant={SUPPLY_STATUS[s].variant}>
                    {SUPPLY_STATUS[s].label} {summary.count[s]}
                  </Badge>
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>비용</CardDescription>
                <CardTitle className="text-2xl tabular-nums">{won(data.cost)}</CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                결제 확인 {won(summary.paid)} · 예상(결제 전) {won(summary.estimated)}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>가장 많이 쓴 품목</CardDescription>
                <CardTitle className="truncate text-2xl">{summary.items[0]?.name ?? "-"}</CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                {summary.items[0] ? `${won(summary.items[0].amount)} · ${summary.items[0].orders}번 발주` : "품목 없음"}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <Card>
              <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
                <CardTitle>발주 목록</CardTitle>
                <div className="flex gap-1" role="group" aria-label="상태로 거르기">
                  {(["all", "pending", "ordered", "done"] as Filter[]).map((f) => (
                    <Button
                      key={f}
                      size="sm"
                      variant={filter === f ? "secondary" : "ghost"}
                      aria-pressed={filter === f}
                      onClick={() => setFilter(f)}
                    >
                      {f === "all" ? "전체" : SUPPLY_STATUS[f].label}
                    </Button>
                  ))}
                </div>
              </CardHeader>
              <CardContent>
                {shown.length === 0 ? (
                  <Empty>조건에 맞는 발주가 없습니다.</Empty>
                ) : (
                  <ul className="divide-y">
                    {shown.map((o) => (
                      <OrderRow key={o.id} order={o} open={open === o.id} onToggle={() => setOpen(open === o.id ? null : o.id)} />
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card className="h-fit">
              <CardHeader>
                <CardTitle>품목별 지출 상위 10</CardTitle>
                <CardDescription>단가표 기준 금액 (결제는 발주 단위로만 기록됩니다)</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>품목</TableHead>
                      <TableHead className="text-right">수량</TableHead>
                      <TableHead className="text-right">금액</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.items.map((it) => (
                      <TableRow key={it.name}>
                        <TableCell>
                          {it.name}
                          <div className="text-xs text-muted-foreground">{it.orders}번 발주</div>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {num(Math.round(it.qty * 100) / 100)}
                          {it.unit ?? ""}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{num(it.amount)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}

function OrderRow({ order: o, open, onToggle }: { order: SupplyOrder; open: boolean; onToggle: () => void }) {
  const diff = o.paid_amount != null && o.estimated_total != null ? o.paid_amount - o.estimated_total : null;
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start gap-3 text-left"
      >
        <Badge variant={SUPPLY_STATUS[o.status].variant}>{SUPPLY_STATUS[o.status].label}</Badge>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm">{o.items.map((i) => i.name).join(", ") || "품목 없음"}</p>
          <p className="text-xs text-muted-foreground">
            {o.requester} · {kstTime(o.created_at)} · 품목 {o.items.length}개
          </p>
        </div>
        <div className="text-right">
          <span className="text-sm font-medium tabular-nums">{won(o.cost)}</span>
          {o.paid_amount == null && <div className="text-xs text-muted-foreground">예상</div>}
        </div>
        <ChevronDown className={cn("mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="mt-3 grid gap-3 rounded-md bg-muted/50 p-3 text-sm">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>품목</TableHead>
                <TableHead className="text-right">수량</TableHead>
                <TableHead className="text-right">단가</TableHead>
                <TableHead className="text-right">소계</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {o.items.map((it, i) => (
                <TableRow key={i}>
                  <TableCell>{it.name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {num(it.quantity)}
                    {it.unit ?? ""}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{num(it.unit_price)}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(it.subtotal)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            <dt className="text-muted-foreground">예상 금액</dt>
            <dd className="tabular-nums">{won(o.estimated_total)} (단가표 기준)</dd>
            <dt className="text-muted-foreground">결제</dt>
            <dd className="tabular-nums">
              {o.paid_amount == null
                ? "아직 기록 없음"
                : `${won(o.paid_amount)}${o.paid_on ? ` · ${md(o.paid_on)}` : ""}${o.card_label ? ` · ${o.card_label}` : ""}`}
              {diff != null && diff !== 0 && (
                <span className="ml-1 text-muted-foreground">(예상보다 {diff > 0 ? `${num(diff)}원 많음` : `${num(-diff)}원 적음`})</span>
              )}
            </dd>
            {o.memo && (
              <>
                <dt className="text-muted-foreground">메모</dt>
                <dd className="whitespace-pre-wrap">{o.memo}</dd>
              </>
            )}
          </dl>
        </div>
      )}
    </li>
  );
}
