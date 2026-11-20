"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { CircleAlert, Trash2 } from "lucide-react";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, fetcher, type Expense, type ExpenseCategory, type Expenses, type Profit, type AdjustmentKind } from "@/lib/api";
import { md, monthLabel, todayKst, won } from "@/lib/format";
import { EXPENSE_CATEGORY } from "@/lib/labels";
import { cn } from "@/lib/utils";

const CATEGORIES = Object.keys(EXPENSE_CATEGORY) as ExpenseCategory[];

// 손익: 한 달(달력 기준) 매출 − 비용. 비용 = 직접 입력한 지출 + 발주 앱 + 인건비 (backend reports.profit)
export default function ProfitPage() {
  const [month, setMonth] = useState(() => todayKst().slice(0, 7));
  const profit = useSWR<Profit>(`/api/profit?month=${month}`, fetcher);
  const expenses = useSWR<Expenses>(`/api/expenses?month=${month}`, fetcher);
  const refresh = () => Promise.all([profit.mutate(), expenses.mutate()]);

  const onAdded = (e: Expense) => {
    const m = e.spent_on.slice(0, 7);
    if (m !== month) setMonth(m); // 다른 달 지출을 넣었으면 그 달로 옮겨 보여 준다
    else refresh();
  };

  return (
    <>
      <PageHeader title="손익" description="한 달 매출(홀 매출 + 외상 정산)에서 지출·발주·인건비를 뺀 금액입니다.">
        <MonthPicker value={month} onChange={setMonth} />
      </PageHeader>

      <div className="grid gap-4">
        {profit.error ? (
          <ErrorBox error={profit.error} onRetry={() => profit.mutate()} />
        ) : !profit.data ? (
          <Loading rows={4} />
        ) : (
          <Summary p={profit.data} />
        )}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <ExpenseForm onAdded={onAdded} />
          <Card>
            <CardHeader>
              <CardTitle>{monthLabel(month)} 지출</CardTitle>
              <CardDescription>
                {expenses.data ? `${expenses.data.expenses.length}건 · ${won(expenses.data.total)}` : " "}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {expenses.error ? (
                <ErrorBox error={expenses.error} onRetry={() => expenses.mutate()} />
              ) : !expenses.data ? (
                <Loading rows={3} />
              ) : expenses.data.expenses.length === 0 ? (
                <Empty>{monthLabel(month)}에 입력한 지출이 없습니다.</Empty>
              ) : (
                <ExpenseList rows={expenses.data.expenses} onDeleted={refresh} />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}

/** 보정 메모를 이어 붙인다 (매출 화면에서 넣은 것) */
function adjustmentNotes(p: Profit, kind: AdjustmentKind) {
  return p.adjustments.filter((a) => a.kind === kind).map((a) => a.memo).join(" · ");
}

function Summary({ p }: { p: Profit }) {
  const costRows: [string, number, string?][] = [
    ...CATEGORIES.map((c) => [EXPENSE_CATEGORY[c], p.costs[c], "직접 입력"] as [string, number, string]),
    ["발주 앱", p.costs.supply_app, "결제 금액, 없으면 예상 금액"],
    ["인건비", p.costs.labor, `급여 기간 ${md(p.pay_start)}~${md(p.pay_end)}`],
  ];
  const posBasis = p.pos_last_date ? `POS 실매출 ${p.pos_days}일 · ${md(p.pos_last_date)}까지` : "매출 화면에서 POS 엑셀 올리기";
  const revenueRows: [string, string, string, boolean?][] = [
    ["홀 매출 (POS)", p.revenue.pos == null ? "올리기 전" : won(p.revenue.pos), posBasis, p.revenue.pos == null],
    ...(p.pos_connected
      ? [["POS에 찍은 외상", p.revenue.pos_credit == null ? "-" : `−${won(p.revenue.pos_credit)}`,
          "주문확인용. 거래처가 낸 지난 외상값이라 뺀다", p.revenue.pos_credit == null] as [string, string, string, boolean]]
      : []),
    ...(p.revenue.credit_paid_other
      ? [["다른 메뉴로 찍은 외상 결제", `−${won(p.revenue.credit_paid_other)}`, adjustmentNotes(p, "credit_paid")] as [string, string, string]]
      : []),
    ["외상 정산", won(p.revenue.credit), "정산처 식대, 달력 기준"],
    ...(p.revenue.credit_missing
      ? [["정산 앱에서 지운 외상", `+${won(p.revenue.credit_missing)}`, adjustmentNotes(p, "credit_missing")] as [string, string, string]]
      : []),
  ];
  const loss = p.profit < 0;
  const today = todayKst();
  const inProgress = p.start <= today && today <= p.end;
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat title="매출" value={won(p.revenue_total)} foot={(p.pos_connected ? "홀 매출(POS) + 외상 정산" : "외상 정산만 (홀 매출 올리기 전)") + (p.adjustments.length ? " ± 보정" : "")} />
        <Stat title="비용" value={won(p.cost_total)} foot={`지출 ${p.expense_count}건 · 발주 · 인건비`} />
        <Stat
          title="손익"
          value={won(p.profit)}
          foot={loss ? "비용이 매출보다 많습니다" : "매출 − 비용"}
          valueClass={loss ? "text-destructive" : undefined}
          testId="profit-value"
        />
      </div>

      {(!p.pos_connected || p.credit_unchecked || inProgress) && (
        <div className="grid gap-1 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {inProgress && (
            <p className="flex items-start gap-1.5">
              <CircleAlert className="mt-px size-3.5 shrink-0" />
              진행 중인 달입니다. 매출은 {md(today)}까지 쌓였지만 임대료·인건비처럼 한 번에 나가는 돈은 이미 들어가 있습니다.
            </p>
          )}
          {!p.pos_connected && (
            <p className="flex items-start gap-1.5">
              <CircleAlert className="mt-px size-3.5 shrink-0" />
              <span>
                이 달 홀 매출(POS)을 아직 올리지 않아 매출에 빠져 있습니다. 지금 손익은 외상 정산만 기준으로 한 값입니다.{" "}
                <Link href="/sales" className="underline underline-offset-2">매출 화면에서 올리기</Link>
              </span>
            </p>
          )}
          {p.credit_unchecked && (
            <p className="flex items-start gap-1.5">
              <CircleAlert className="mt-px size-3.5 shrink-0" />
              <span>
                {p.pos_menu_until
                  ? `메뉴별 자료(상품 ABC 분석)가 ${md(p.pos_menu_until)}까지라, 그 뒤 POS에 찍은 외상(주문확인용)은 빼지 못했습니다.`
                  : "메뉴별 자료(상품 ABC 분석)가 없어 POS에 찍은 외상(주문확인용)을 빼지 못했습니다."}{" "}
                외상 정산과 겹쳐 매출이 그만큼 크게 잡혔을 수 있습니다.{" "}
                <Link href="/sales" className="underline underline-offset-2">매출 화면에서 올리기</Link>
              </span>
            </p>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>항목별</CardTitle>
          <CardDescription>
            발주 앱에 결제를 남긴 장보기를 지출에도 넣으면 두 번 계산됩니다. 한쪽에만 넣어 주세요.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>항목</TableHead>
                <TableHead className="hidden sm:table-cell">기준</TableHead>
                <TableHead className="text-right">금액</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <GroupRow label="매출" amount={p.revenue_total} />
              {revenueRows.map(([label, value, basis, muted]) => (
                <TableRow key={label}>
                  <TableCell className="pl-6">
                    {label}
                    <Basis mobile>{basis}</Basis>
                  </TableCell>
                  <Basis>{basis}</Basis>
                  <TableCell className={cn("text-right tabular-nums", muted && "text-muted-foreground")}>{value}</TableCell>
                </TableRow>
              ))}
              <GroupRow label="비용" amount={p.cost_total} />
              {costRows.map(([label, amount, basis]) => (
                <TableRow key={label}>
                  <TableCell className="pl-6">
                    {label}
                    <Basis mobile>{basis}</Basis>
                  </TableCell>
                  <Basis>{basis}</Basis>
                  <TableCell className={cn("text-right tabular-nums", amount === 0 && "text-muted-foreground")}>
                    {won(amount)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow className="border-t-2">
                <TableCell className="font-medium">손익</TableCell>
                <TableCell className="hidden sm:table-cell" />
                <TableCell className={cn("text-right font-semibold tabular-nums", loss && "text-destructive")}>
                  {won(p.profit)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}

// '기준' 설명: 넓은 화면에서는 따로 열, 휴대폰에서는 항목 이름 아래 (표가 화면 밖으로 밀리지 않게)
function Basis({ children, mobile }: { children: React.ReactNode; mobile?: boolean }) {
  return mobile ? (
    <span className="block text-xs whitespace-normal text-muted-foreground sm:hidden">{children}</span>
  ) : (
    <TableCell className="hidden text-xs text-muted-foreground sm:table-cell">{children}</TableCell>
  );
}

function GroupRow({ label, amount }: { label: string; amount: number }) {
  return (
    <TableRow className="bg-muted/40">
      <TableCell className="font-medium">{label}</TableCell>
      <TableCell className="hidden sm:table-cell" />
      <TableCell className="text-right font-medium tabular-nums">{won(amount)}</TableCell>
    </TableRow>
  );
}

function Stat(props: { title: string; value: string; foot: string; valueClass?: string; testId?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{props.title}</CardDescription>
        <CardTitle className={cn("text-2xl tabular-nums", props.valueClass)} data-testid={props.testId}>
          {props.value}
        </CardTitle>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">{props.foot}</CardContent>
    </Card>
  );
}

const selectClass =
  "h-8 w-full rounded-lg border border-input bg-transparent px-2 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30";

function ExpenseForm({ onAdded }: { onAdded: (e: Expense) => void }) {
  const [spentOn, setSpentOn] = useState(todayKst);
  const [vendor, setVendor] = useState("");
  const [category, setCategory] = useState<ExpenseCategory>("food");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // "85,000"처럼 쉼표를 넣어 쳐도 숫자만 남긴다
  const value = Number(amount.replace(/[^\d]/g, "")) || 0;

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const saved = await api<Expense>("/api/expenses", {
        method: "POST",
        body: JSON.stringify({ spent_on: spentOn, vendor, category, amount: value, memo: memo || null }),
      });
      setVendor("");
      setAmount("");
      setMemo("");
      onAdded(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>지출 입력</CardTitle>
        <CardDescription>매장·도매상 장보기, 공과금처럼 발주 앱에 없는 돈</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-3" aria-label="지출 입력">
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="spent_on">날짜</Label>
              <Input id="spent_on" type="date" value={spentOn} max={todayKst()} required
                onChange={(e) => setSpentOn(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="category">분류</Label>
              <select id="category" className={selectClass} value={category}
                onChange={(e) => setCategory(e.target.value as ExpenseCategory)}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {EXPENSE_CATEGORY[c]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="vendor">산 곳</Label>
            <Input id="vendor" value={vendor} maxLength={60} required placeholder="예: 가락시장 청과"
              onChange={(e) => setVendor(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="amount">금액 (원)</Label>
            <Input id="amount" inputMode="numeric" value={amount} required placeholder="예: 85000"
              onChange={(e) => setAmount(e.target.value)} />
            {value > 0 && <p className="text-xs text-muted-foreground tabular-nums">{won(value)}</p>}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="memo">메모 (선택)</Label>
            <Input id="memo" value={memo} maxLength={200} onChange={(e) => setMemo(e.target.value)} />
          </div>
          {error && (
            <p role="alert" className="flex items-center gap-1.5 text-sm text-destructive">
              <CircleAlert className="size-4" /> {error}
            </p>
          )}
          <Button type="submit" disabled={saving}>
            {saving ? "저장 중…" : "추가"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ExpenseList({ rows, onDeleted }: { rows: Expense[]; onDeleted: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const remove = async (e: Expense) => {
    if (!window.confirm(`${md(e.spent_on)} ${e.vendor} ${won(e.amount)}을 지울까요?`)) return;
    setBusy(e.id);
    try {
      await api(`/api/expenses/${e.id}`, { method: "DELETE" });
      onDeleted();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "지우지 못했습니다");
    } finally {
      setBusy(null);
    }
  };
  return (
    <ul className="divide-y" aria-label="지출 목록">
      {rows.map((e) => (
        <li key={e.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
          <span className="w-12 shrink-0 text-xs text-muted-foreground tabular-nums">{md(e.spent_on)}</span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm">
              {e.vendor}{" "}
              <Badge variant="outline" className="ml-1 align-middle">
                {EXPENSE_CATEGORY[e.category]}
              </Badge>
            </p>
            {e.memo && <p className="truncate text-xs text-muted-foreground">{e.memo}</p>}
          </div>
          <span className="text-sm font-medium tabular-nums">{won(e.amount)}</span>
          <Button variant="ghost" size="icon" aria-label={`${e.vendor} 지출 지우기`} disabled={busy === e.id}
            onClick={() => remove(e)}>
            <Trash2 />
          </Button>
        </li>
      ))}
    </ul>
  );
}
