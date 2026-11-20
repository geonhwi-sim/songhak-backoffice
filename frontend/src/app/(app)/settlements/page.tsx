"use client";

import { useState } from "react";
import useSWR from "swr";
import { CalendarRange, FileDown, Printer } from "lucide-react";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetcher, type SettlementDetail, type Settlements } from "@/lib/api";
import { lastMonth, md, monthLabel, num, won } from "@/lib/format";
import { cn } from "@/lib/utils";

export default function SettlementsPage() {
  const [month, setMonth] = useState(lastMonth);
  const [placeId, setPlaceId] = useState<number | null>(null);
  const { data, error, isLoading, mutate } = useSWR<Settlements>(`/api/settlements?month=${month}`, fetcher);

  const changeMonth = (m: string) => {
    setMonth(m);
    setPlaceId(null);
  };

  return (
    <>
      <PageHeader title="정산" description="달력 기준 1일 ~ 말일. 정산서의 '기간'으로 다음 달 며칠까지 묶어 뽑을 수도 있습니다.">
        <MonthPicker value={month} onChange={changeMonth} />
      </PageHeader>

      {error ? (
        <ErrorBox error={error} onRetry={() => mutate()} />
      ) : isLoading || !data ? (
        <Loading />
      ) : data.places.length === 0 ? (
        <Empty>{monthLabel(month)} 정산 기록이 없습니다.</Empty>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <Card className="h-fit print:hidden">
            <CardHeader>
              <CardTitle>정산처별 합계</CardTitle>
              <CardDescription>정산처를 누르면 정산서가 열립니다</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>정산처</TableHead>
                    <TableHead className="text-right">주문</TableHead>
                    <TableHead className="text-right">금액</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.places.map((p) => (
                    <TableRow
                      key={p.place_id}
                      onClick={() => setPlaceId(p.place_id)}
                      className={cn("cursor-pointer", placeId === p.place_id && "bg-muted")}
                    >
                      <TableCell>
                        <button type="button" className="text-left font-medium" aria-pressed={placeId === p.place_id}>
                          {p.name}
                        </button>
                        <div className="mt-0.5 flex gap-1">
                          {p.estimated_items > 0 && <Badge variant="outline">추정 {p.estimated_items}</Badge>}
                          {p.review_items > 0 && <Badge variant="secondary">확인 필요 {p.review_items}</Badge>}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{p.orders}건</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {won(p.amount)}
                        {p.discount > 0 && (
                          <div className="text-xs text-muted-foreground">할인 {num(p.discount)}</div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                <TableFooter>
                  <TableRow>
                    <TableCell colSpan={2}>합계</TableCell>
                    <TableCell className="text-right font-bold tabular-nums">{won(data.total)}</TableCell>
                  </TableRow>
                </TableFooter>
              </Table>
            </CardContent>
          </Card>

          {placeId ? (
            <Invoice key={`${placeId}-${month}`} placeId={placeId} month={month} />
          ) : (
            <Empty>왼쪽에서 정산처를 고르세요.</Empty>
          )}
        </div>
      )}
    </>
  );
}

// 기간 정산서: 거래처가 다음 달 며칠분까지 함께 낼 때 (예: 9/1 ~ 10/2). 정산 앱에서 주문을 지우지 않아도 된다
const MAX_DAYS = 93;

function rangeError(start: string, end: string) {
  if (!start || !end) return "시작일과 끝 날짜를 모두 고르세요.";
  if (start > end) return "시작일이 끝 날짜보다 늦습니다.";
  const days = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
  if (days > MAX_DAYS) return `기간은 ${MAX_DAYS}일까지 고를 수 있습니다.`;
  return null;
}

function Invoice({ placeId, month }: { placeId: number; month: string }) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState(`${month}-01`);
  const [end, setEnd] = useState("");
  const [range, setRange] = useState<{ start: string; end: string } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const query = range ? `start=${range.start}&end=${range.end}` : `month=${month}`;
  const { data, error, isLoading } = useSWR<SettlementDetail>(`/api/settlements/${placeId}?${query}`, fetcher);

  const apply = (ev: React.FormEvent) => {
    ev.preventDefault();
    const err = rangeError(start, end);
    setFormError(err);
    if (!err) setRange({ start, end });
  };

  const picker = open && (
    <form onSubmit={apply} aria-label="정산 기간 고르기"
      className="grid gap-3 rounded-lg border bg-muted/30 p-3 text-sm print:hidden sm:grid-cols-[1fr_1fr_auto] sm:items-end">
      <div className="grid gap-1.5">
        <Label htmlFor="inv-start">시작일</Label>
        <Input id="inv-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="inv-end">끝 날짜 (포함)</Label>
        <Input id="inv-end" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm">이 기간으로 보기</Button>
        {range && (
          <Button type="button" size="sm" variant="ghost"
            onClick={() => { setRange(null); setOpen(false); setFormError(null); }}>
            한 달로
          </Button>
        )}
      </div>
      {formError && <p role="alert" className="text-destructive sm:col-span-3">{formError}</p>}
    </form>
  );

  if (error) return <div className="grid gap-3">{picker}<ErrorBox error={error} /></div>;
  if (isLoading || !data) return <Loading rows={8} />;

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle>{data.name} 정산서</CardTitle>
          <CardDescription data-testid="invoice-period">
            {range ? "직접 고른 기간" : monthLabel(month)} ({md(data.start)} ~ {md(data.end)}) · 주문 {data.orders.length}건
          </CardDescription>
        </div>
        <div className="flex gap-2 print:hidden">
          <Button variant={open ? "secondary" : "outline"} size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
            <CalendarRange />
            기간
          </Button>
          {/* 같은 출처(/api)라 로그인 쿠키가 따라간다. 거래처에 보내는 문서라 화면의 내부 표시는 빠진다 */}
          <a
            href={`/api/settlements/${placeId}/pdf?${query}`}
            download
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            <FileDown />
            PDF
          </a>
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer />
            인쇄
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        {picker}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>날짜</TableHead>
              <TableHead>항목</TableHead>
              <TableHead className="text-right">수량</TableHead>
              <TableHead className="text-right">단가</TableHead>
              <TableHead className="text-right">금액</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.orders.flatMap((o) =>
              o.items.map((it, i) => (
                <TableRow key={`${o.order_id}-${i}`}>
                  <TableCell className="tabular-nums text-muted-foreground">{i === 0 ? md(o.date) : ""}</TableCell>
                  <TableCell>
                    {it.name}
                    {it.option && <span className="text-muted-foreground"> · {it.option}</span>}
                    {it.is_discounted && <span className="ml-1 text-xs text-primary">할인</span>}
                    {it.needs_review && (
                      <Badge variant="secondary" className="ml-1">
                        확인 필요
                      </Badge>
                    )}
                    {it.price_source === "estimated" && (
                      <Badge variant="outline" className="ml-1">
                        추정 가격
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{it.qty}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(it.unit_price)}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(it.amount)}</TableCell>
                </TableRow>
              )),
            )}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TableCell colSpan={4}>합계</TableCell>
              <TableCell className="text-right font-bold tabular-nums">{won(data.total)}</TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </CardContent>
    </Card>
  );
}
