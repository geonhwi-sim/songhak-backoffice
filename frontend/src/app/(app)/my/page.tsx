"use client";

import { useState } from "react";
import useSWR from "swr";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetcher, type MyPay } from "@/lib/api";
import { currentPayMonth, hours, md, monthLabel, num, won } from "@/lib/format";

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];
// 날짜만 있는 값이라 UTC 자정으로 읽어 요일을 구한다 (보는 사람의 시간대와 상관없게)
const weekday = (d: string) => WEEKDAY[new Date(`${d}T00:00:00Z`).getUTCDay()];

// 직원 계정 화면: 본인 근무 기록과 (예상) 급여. 가게에서 휴대폰으로 보는 경우가 많아 좁은 화면을 먼저 생각했다.
export default function MyPage() {
  const [month, setMonth] = useState(currentPayMonth);
  const { data, error, isLoading, mutate } = useSWR<MyPay>(`/api/me/pay?month=${month}`, fetcher);

  return (
    <>
      <PageHeader
        title={data ? `${data.name}님의 근무` : "내 근무"}
        description={
          data
            ? `${monthLabel(month)} 급여 · ${md(data.start)} ~ ${md(data.end)} (전월 17일 ~ 당월 16일)`
            : "급여 기간은 전월 17일 ~ 당월 16일입니다"
        }
      >
        <MonthPicker value={month} onChange={setMonth} />
      </PageHeader>

      {error ? (
        <ErrorBox error={error} onRetry={() => mutate()} />
      ) : isLoading || !data ? (
        <Loading rows={4} />
      ) : (
        <div className="grid gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm font-normal text-muted-foreground">
                {monthLabel(month)} 급여
                {data.is_estimate && <Badge variant="outline">예상 · 기간이 끝나지 않음</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4">
              <p className="text-3xl font-bold tabular-nums" aria-label="급여 합계">
                {won(data.total)}
              </p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
                <Stat label="근무일" value={`${data.days}일`} />
                <Stat label="근무시간" value={hours(data.hours)} />
                <Stat label="기본급" value={won(data.base_pay)} />
                <Stat label={`주휴수당 (${data.juhu_weeks}주)`} value={won(data.juhu_pay)} />
              </dl>
              <p className="text-xs text-muted-foreground">
                주휴수당: 한 주(월~일) 15시간 이상 근무하면 그 주의 일요일이 속한 급여 기간에 한 번 들어갑니다.
                {data.is_estimate && " 기간이 끝날 때까지 근무가 늘면 금액도 바뀝니다."}
              </p>
            </CardContent>
          </Card>

          {data.logs.length === 0 ? (
            <Empty>{monthLabel(month)} 급여 기간에 근무 기록이 없습니다.</Empty>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>근무 기록 {data.logs.length}일</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>날짜</TableHead>
                      <TableHead>시간</TableHead>
                      <TableHead className="text-right">근무</TableHead>
                      <TableHead className="text-right">금액</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.logs.map((l, i) => (
                      <TableRow key={`${l.date}-${i}`}>
                        <TableCell className="tabular-nums">
                          {md(l.date)} <span className="text-muted-foreground">({weekday(l.date)})</span>
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {l.start}~{l.end}
                          {l.break_min > 0 && <div className="text-xs text-muted-foreground">휴게 {l.break_min}분</div>}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{hours(l.hours)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {num(l.pay)}
                          <div className="text-xs text-muted-foreground">시급 {num(l.hourly_rate)}</div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}
