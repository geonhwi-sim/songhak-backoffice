"use client";

import { useRef, useState } from "react";
import useSWR from "swr";
import { CircleAlert, CircleCheck, FileSpreadsheet, Trash2 } from "lucide-react";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  api,
  fetcher,
  uploadPos,
  type Adjustment,
  type AdjustmentKind,
  type PosImport,
  type PosPreview,
  type PosSales,
} from "@/lib/api";
import { kstTime, md, monthLabel, num, todayKst, won } from "@/lib/format";
import { cn } from "@/lib/utils";

const KIND = { daily: "POS정산내역 (일별)", menu: "상품 ABC 분석 (메뉴별)" } as const;
const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
const weekday = (d: string) => WEEKDAY[new Date(Number(d.slice(0, 4)), Number(d.slice(5, 7)) - 1, Number(d.slice(8, 10))).getDay()];
const period = (a: string, b: string) => `${md(a)}~${md(b)}`;

// 홀 매출: OK POS에서 내려받은 엑셀(일별 정산내역, 메뉴별 상품 분석)을 올려서 본다 (backend app/pos_import.py)
export default function SalesPage() {
  const [month, setMonth] = useState(() => todayKst().slice(0, 7));
  const sales = useSWR<PosSales>(`/api/pos/sales?month=${month}`, fetcher);

  const onImported = (p: PosPreview) => {
    const m = p.start.slice(0, 7);
    if (m !== month) setMonth(m); // 다른 달 파일이면 그 달로 옮겨 보여 준다
    else sales.mutate();
  };

  return (
    <>
      <PageHeader title="매출" description="OK POS에서 내려받은 엑셀로 본 홀 매출입니다. 손익의 매출로 쓰입니다.">
        <MonthPicker value={month} onChange={setMonth} />
      </PageHeader>

      <div className="grid gap-4">
        {sales.error ? (
          <ErrorBox error={sales.error} onRetry={() => sales.mutate()} />
        ) : !sales.data ? (
          <Loading rows={4} />
        ) : (
          <Summary s={sales.data} />
        )}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="grid h-fit gap-4">
            <UploadCard onImported={onImported} imports={sales.data?.imports ?? []} />
            <AdjustmentCard month={month} items={sales.data?.adjustments} onChange={() => sales.mutate()} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle>{monthLabel(month)} 일별</CardTitle>
              <CardDescription>영업일 하루 마감의 실매출 (포스 합계)</CardDescription>
            </CardHeader>
            <CardContent>
              {!sales.data ? (
                <Loading rows={3} />
              ) : sales.data.days.length === 0 ? (
                <Empty>{monthLabel(month)}에 올린 POS정산내역이 없습니다.</Empty>
              ) : (
                <DayList days={sales.data.days} />
              )}
            </CardContent>
          </Card>
        </div>

        {sales.data && <MenuTable s={sales.data} />}
      </div>
    </>
  );
}

function Summary({ s }: { s: PosSales }) {
  const avg = s.day_count ? Math.round(s.net_total / s.day_count) : null;
  const bad = s.checks.filter((c) => !c.match);
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat title="POS 실매출" value={won(s.net_total)}
          foot={s.last_date ? `${s.day_count}일 · ${md(s.last_date)}까지` : "올린 자료 없음"} testId="pos-net" />
        <Stat title="하루 평균" value={won(avg)} foot={`현금 ${won(s.cash_total)}`} />
        <Stat title="POS에 찍은 외상" value={won(s.menu_until ? s.credit_item : null)}
          foot={s.menu_until ? "주문확인용 · 거래처가 낸 지난 외상값이라 손익에서 뺍니다" : "메뉴별 자료를 올리면 보입니다"} />
      </div>

      {s.checks.length > 0 && (
        <div
          role="status"
          className={cn(
            "grid gap-1 rounded-lg border px-3 py-2 text-xs",
            bad.length ? "border-amber-300 bg-amber-50 text-amber-900" : "border-emerald-200 bg-emerald-50 text-emerald-900",
          )}
        >
          {s.checks.map((c) => (
            <p key={c.start} className="flex items-start gap-1.5">
              {c.match ? <CircleCheck className="mt-px size-3.5 shrink-0" /> : <CircleAlert className="mt-px size-3.5 shrink-0" />}
              {c.match
                ? `${period(c.start, c.end)} 일별 합계와 메뉴별 합계가 같습니다 (${won(c.daily_net)}).`
                : `${period(c.start, c.end)} 일별 합계 ${won(c.daily_net)}와 메뉴별 합계 ${won(c.menu_net)}가 ${won(Math.abs(c.daily_net - c.menu_net))} 다릅니다. 같은 기간으로 두 파일을 다시 받아 올려 주세요.`}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function Stat(props: { title: string; value: string; foot: string; testId?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{props.title}</CardDescription>
        <CardTitle className="text-2xl tabular-nums" data-testid={props.testId}>
          {props.value}
        </CardTitle>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">{props.foot}</CardContent>
    </Card>
  );
}

function UploadCard({ onImported, imports }: { onImported: (p: PosPreview) => void; imports: PosImport[] }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PosPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setFile(null);
    setPreview(null);
    if (input.current) input.current.value = "";
  };

  const pick = async (f: File | undefined) => {
    setError(null);
    setDone(null);
    setPreview(null);
    setFile(f ?? null);
    if (!f) return;
    setBusy(true);
    try {
      setPreview(await uploadPos("/api/pos/preview", f));
    } catch (e) {
      setError(e instanceof Error ? e.message : "파일을 읽지 못했습니다");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await uploadPos("/api/pos/import", file);
      setDone(`${KIND[saved.kind]} ${period(saved.start, saved.end)}을 넣었습니다.`);
      reset();
      onImported(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "넣지 못했습니다");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>POS 엑셀 올리기</CardTitle>
        <CardDescription>
          OK POS 웹 관리자에서 &lsquo;POS정산내역&rsquo;과 &lsquo;상품 ABC 분석&rsquo;을 같은 기간(한 달)으로 내려받아 하나씩 올려
          주세요. 같은 기간을 다시 올리면 바꿉니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="pos-file">엑셀 파일 (.xlsx)</Label>
          <input
            ref={input}
            id="pos-file"
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            disabled={busy}
            onChange={(e) => pick(e.target.files?.[0])}
            className="text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-transparent file:px-3 file:py-1.5 file:text-sm"
          />
        </div>

        {busy && !preview && <p className="text-sm text-muted-foreground">읽는 중…</p>}
        {preview && <PreviewBox p={preview} />}

        {error && (
          <p role="alert" className="flex items-start gap-1.5 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" /> {error}
          </p>
        )}
        {done && (
          <p role="status" className="flex items-center gap-1.5 text-sm text-emerald-700">
            <CircleCheck className="size-4" /> {done}
          </p>
        )}

        {preview && (
          <div className="flex gap-2">
            <Button onClick={confirm} disabled={busy}>
              {busy ? "넣는 중…" : preview.replaces.rows ? "바꿔 넣기" : "넣기"}
            </Button>
            <Button variant="outline" onClick={reset} disabled={busy}>
              취소
            </Button>
          </div>
        )}

        {imports.length > 0 && (
          <div className="grid gap-1.5 border-t pt-3">
            <p className="text-xs font-medium text-muted-foreground">최근 올린 파일</p>
            <ul className="grid gap-1 text-xs" aria-label="최근 올린 파일">
              {imports.map((x) => (
                <li key={x.id} className="flex items-center gap-2">
                  <FileSpreadsheet className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">
                    {x.kind === "daily" ? "일별" : "메뉴별"} {period(x.start, x.end)}
                    {x.file_name && <span className="text-muted-foreground"> · {x.file_name}</span>}
                  </span>
                  <span className="shrink-0 text-muted-foreground tabular-nums">{kstTime(x.imported_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PreviewBox({ p }: { p: PosPreview }) {
  const rows: [string, string][] =
    p.kind === "daily"
      ? [
          ["기간", `${p.start} ~ ${p.end}`],
          ["마감한 날", `${p.days}일 (${md(p.first!)}~${md(p.last!)})`],
          ["총매출 − 할인", `${won(p.gross_total)} − ${won(p.discount_total)}`],
          ["실매출 합계", won(p.net_total)],
        ]
      : [
          ["기간", `${p.start} ~ ${p.end}`],
          ["메뉴", `${p.rows}개 · ${num(p.qty_total)}개 판매`],
          ["실매출 합계", won(p.net_total)],
          ["주문확인용 (외상)", won(p.credit_total)],
        ];
  return (
    <div className="grid gap-2 rounded-lg border bg-muted/30 p-3 text-sm" aria-label="미리보기">
      <p className="font-medium">{KIND[p.kind]}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="text-right tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {p.replaces.rows > 0 && (
        <p className="flex items-start gap-1.5 text-xs text-amber-800">
          <CircleAlert className="mt-px size-3.5 shrink-0" />
          {p.replaces.periods?.length
            ? `이미 올린 ${p.replaces.periods.map((x) => period(x.start, x.end)).join(", ")} 메뉴별 자료(${won(p.replaces.net_total)})를 지우고 바꿉니다.`
            : `이 기간에 이미 올린 ${p.replaces.rows}일 자료(${won(p.replaces.net_total)})를 바꿉니다.`}
        </p>
      )}
      {p.warnings.map((w) => (
        <p key={w} className="flex items-start gap-1.5 text-xs text-amber-800">
          <CircleAlert className="mt-px size-3.5 shrink-0" /> {w}
        </p>
      ))}
    </div>
  );
}

function DayList({ days }: { days: PosSales["days"] }) {
  const max = Math.max(...days.map((d) => d.net), 1);
  return (
    <ul className="grid gap-1" aria-label="일별 매출">
      {days.map((d) => (
        <li key={d.date} className="grid grid-cols-[3.25rem_minmax(0,1fr)_auto] items-center gap-2 text-sm">
          <span className={cn("text-xs tabular-nums", weekday(d.date) === "토" ? "text-sky-700" : "text-muted-foreground")}>
            {md(d.date)} {weekday(d.date)}
          </span>
          <span className="h-2 rounded-full bg-muted">
            <span className="block h-2 rounded-full bg-primary/70" style={{ width: `${(d.net / max) * 100}%` }} />
          </span>
          <span className="w-24 text-right tabular-nums">{won(d.net)}</span>
        </li>
      ))}
    </ul>
  );
}

const MENU_TOP = 15;

function MenuTable({ s }: { s: PosSales }) {
  const [all, setAll] = useState(false);
  const shown = all ? s.menu : s.menu.slice(0, MENU_TOP);
  return (
    <Card>
      <CardHeader>
        <CardTitle>메뉴별</CardTitle>
        <CardDescription>
          {s.menu_periods.length
            ? `${s.menu_periods.map((x) => period(x.start, x.end)).join(", ")} 상품 분석 · 메뉴 ${s.menu.length}개`
            : "이 달 안의 기간으로 받은 상품 ABC 분석이 없습니다."}
        </CardDescription>
      </CardHeader>
      {s.menu.length > 0 && (
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>메뉴</TableHead>
                <TableHead className="hidden sm:table-cell">대분류</TableHead>
                <TableHead className="text-right">수량</TableHead>
                <TableHead className="text-right">실매출</TableHead>
                <TableHead className="hidden text-right sm:table-cell">비율</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((x) => (
                <TableRow key={x.code} className={x.is_credit ? "bg-amber-50/60" : undefined}>
                  <TableCell className="whitespace-normal">
                    {x.name}
                    {x.is_credit && (
                      <Badge variant="outline" className="ml-1.5 align-middle">
                        외상 · 손익에서 뺌
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground sm:table-cell">{x.category ?? "-"}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(x.qty)}</TableCell>
                  <TableCell className="text-right tabular-nums">{won(x.net)}</TableCell>
                  <TableCell className="hidden text-right text-muted-foreground tabular-nums sm:table-cell">
                    {s.menu_net_total ? `${((x.net / s.menu_net_total) * 100).toFixed(1)}%` : "-"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {s.menu.length > MENU_TOP && (
            <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAll(!all)}>
              {all ? "상위 15개만 보기" : `전체 ${s.menu.length}개 보기`}
            </Button>
          )}
        </CardContent>
      )}
    </Card>
  );
}

const ADJ: Record<AdjustmentKind, { label: string; sign: string; help: string; example: string }> = {
  credit_paid: {
    label: "다른 메뉴로 찍은 외상 결제",
    sign: "−",
    help: "거래처 외상값을 POS에서 주문확인용이 아닌 메뉴로 찍었을 때. 손익 매출에서 뺍니다.",
    example: "예: ○○ 9월분, 육회비빔밥으로 찍음",
  },
  credit_missing: {
    label: "정산 앱에서 지운 외상",
    sign: "+",
    help: "이달에 먹었지만 다른 달 결제에 함께 받고 정산 앱에서 지운 주문. 손익 매출에 더합니다.",
    example: "예: ○○ 10/1~10/2, 9월분과 같이 받음",
  },
};
const ADJ_KINDS = Object.keys(ADJ) as AdjustmentKind[];

// 손익 매출 보정: POS와 정산 앱만으로는 맞출 수 없는 외상을 사장님이 달마다 적는다 (backend models.RevenueAdjustment)
function AdjustmentCard({ month, items, onChange }: { month: string; items?: Adjustment[]; onChange: () => void }) {
  const [kind, setKind] = useState<AdjustmentKind>("credit_paid");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const value = Number(amount.replace(/[^\d]/g, "")) || 0; // "150,000"처럼 쳐도 숫자만

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/api/revenue-adjustments", { method: "POST", body: JSON.stringify({ month, kind, amount: value, note }) });
      setAmount("");
      setNote("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "저장하지 못했습니다");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (a: Adjustment) => {
    if (!window.confirm(`${ADJ[a.kind].label} ${won(a.amount)}을 지울까요?`)) return;
    setError(null);
    try {
      await api(`/api/revenue-adjustments/${a.id}`, { method: "DELETE" });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "지우지 못했습니다");
    }
  };

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>{monthLabel(month)} 외상 보정</CardTitle>
        <CardDescription>POS·정산 앱 기록만으로 맞지 않는 외상을 적으면 손익 매출에 반영됩니다.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {items && items.length > 0 && (
          <ul className="grid gap-1.5 text-sm" aria-label="외상 보정 목록">
            {items.map((a) => (
              <li key={a.id} className="flex items-start gap-2">
                <span className="min-w-0 flex-1">
                  <span className="text-muted-foreground">{ADJ[a.kind].label}</span>
                  <span className="block truncate text-xs text-muted-foreground">{a.memo}</span>
                </span>
                <span className="shrink-0 tabular-nums">
                  {ADJ[a.kind].sign}
                  {won(a.amount)}
                </span>
                <button type="button" onClick={() => remove(a)} aria-label={`${a.memo} 보정 지우기`}
                  className="shrink-0 text-muted-foreground hover:text-destructive">
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={submit} className="grid gap-3 border-t pt-3" aria-label="외상 보정 넣기">
          <fieldset className="grid gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">종류</legend>
            {ADJ_KINDS.map((k) => (
              <label key={k} className="flex items-start gap-2 text-sm">
                <input type="radio" name="adj-kind" value={k} checked={kind === k} onChange={() => setKind(k)}
                  className="mt-1" />
                <span>
                  {ADJ[k].label} <span className="text-muted-foreground">({ADJ[k].sign})</span>
                  <span className="block text-xs text-muted-foreground">{ADJ[k].help}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="grid gap-1.5">
            <Label htmlFor="adj-amount">금액 (원)</Label>
            <Input id="adj-amount" inputMode="numeric" value={amount} required placeholder="예: 150000"
              onChange={(e) => setAmount(e.target.value)} />
            {value > 0 && <p className="text-xs text-muted-foreground tabular-nums">{won(value)}</p>}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="adj-note">메모</Label>
            <Input id="adj-note" value={note} maxLength={200} required placeholder={ADJ[kind].example}
              onChange={(e) => setNote(e.target.value)} />
          </div>
          {error && (
            <p role="alert" className="flex items-start gap-1.5 text-sm text-destructive">
              <CircleAlert className="mt-0.5 size-4 shrink-0" /> {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? "저장 중…" : "보정 추가"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
