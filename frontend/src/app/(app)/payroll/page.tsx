"use client";

import { useState } from "react";
import useSWR from "swr";

import { Empty, ErrorBox, Loading, MonthPicker, PageHeader } from "@/components/common";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetcher, type Payroll } from "@/lib/api";
import { currentPayMonth, hours, md, monthLabel, won } from "@/lib/format";

export default function PayrollPage() {
  const [month, setMonth] = useState(currentPayMonth);
  const { data, error, isLoading, mutate } = useSWR<Payroll>(`/api/payroll?month=${month}`, fetcher);

  return (
    <>
      <PageHeader
        title="인건비"
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
        <Loading />
      ) : data.employees.length === 0 ? (
        <Empty>{monthLabel(month)} 급여 기간에 근무 기록이 없습니다.</Empty>
      ) : (
        <Card>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>직원</TableHead>
                  <TableHead className="text-right">근무일</TableHead>
                  <TableHead className="text-right">근무시간</TableHead>
                  <TableHead className="text-right">기본급</TableHead>
                  <TableHead className="text-right">주휴수당</TableHead>
                  <TableHead className="text-right">합계</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.employees.map((e) => (
                  <TableRow key={e.employee_id}>
                    <TableCell className="font-medium">{e.name}</TableCell>
                    <TableCell className="text-right tabular-nums">{e.days}일</TableCell>
                    <TableCell className="text-right tabular-nums">{hours(e.hours)}</TableCell>
                    <TableCell className="text-right tabular-nums">{won(e.base_pay)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {won(e.juhu_pay)}
                      <div className="text-xs text-muted-foreground">{e.juhu_weeks}주</div>
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">{won(e.total)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
              <TableFooter>
                <TableRow>
                  <TableCell colSpan={5}>합계</TableCell>
                  <TableCell className="text-right font-bold tabular-nums">{won(data.total)}</TableCell>
                </TableRow>
              </TableFooter>
            </Table>
            <p className="mt-4 text-xs text-muted-foreground">
              주휴수당: 한 주(월~일) 15시간 이상 근무하면 그 주의 일요일이 속한 급여 기간에 한 번 넣습니다.
            </p>
          </CardContent>
        </Card>
      )}
    </>
  );
}
