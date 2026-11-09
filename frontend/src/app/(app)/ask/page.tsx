"use client";

import { useState } from "react";
import useSWR from "swr";
import { ArrowUp, CircleAlert, Code } from "lucide-react";

import { PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, fetcher, type AskQuota, type AskResult } from "@/lib/api";

const EXAMPLES = [
  "지난달 정산처별 금액 알려줘",
  "10월 급여 총액이 얼마야?",
  "9월에 가장 많이 나간 메뉴는?",
  "8월에 할인으로 깎아 준 금액은?",
];

type Entry = { question: string; result?: AskResult; error?: string };

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const pending = entries.length > 0 && !entries[0].result && !entries[0].error;
  // 하루 질문 제한(데모 서버)이 있을 때만 남은 횟수를 보여 준다
  const { data: quota, mutate: setQuota } = useSWR<AskQuota>("/api/ask/quota", fetcher);
  const limited = quota?.limit != null;
  const usedUp = limited && quota.remaining === 0;

  async function ask(q: string) {
    q = q.trim();
    if (q.length < 2 || pending) return;
    setQuestion("");
    setEntries((es) => [{ question: q }, ...es]);
    const done = (patch: Partial<Entry>) => setEntries((es) => [{ ...es[0], ...patch }, ...es.slice(1)]);
    try {
      const result = await api<AskResult>("/api/ask", { method: "POST", body: JSON.stringify({ question: q }) });
      done({ result });
      if (result.remaining_today != null && quota) setQuota({ ...quota, remaining: result.remaining_today }, false);
    } catch (err) {
      done({ error: err instanceof ApiError ? err.message : "서버에 연결하지 못했습니다" });
      if (err instanceof ApiError && err.status === 429 && quota) setQuota({ ...quota, remaining: 0 }, false);
    }
  }

  return (
    <>
      <PageHeader
        title="질문하기"
        description="정산·인건비·발주 데이터에 대해 물어보세요. 답과 함께 실제로 실행한 SQL과 결과 표를 보여 줍니다."
      >
        {limited && (
          <Badge variant="outline" className="tabular-nums">
            오늘 남은 질문 {quota.remaining}/{quota.limit}
          </Badge>
        )}
      </PageHeader>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
        className="flex gap-2"
      >
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="예: 9월 정산 합계가 얼마야?"
          maxLength={300}
          aria-label="질문"
          className="h-10"
        />
        <Button type="submit" size="lg" className="h-10" disabled={pending || usedUp || question.trim().length < 2}>
          <ArrowUp />
          묻기
        </Button>
      </form>

      {entries.length === 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <Button key={ex} variant="outline" size="sm" disabled={usedUp} onClick={() => ask(ex)}>
              {ex}
            </Button>
          ))}
        </div>
      )}

      <div className="mt-6 grid gap-4">
        {entries.map((e, i) => (
          <Answer key={entries.length - i} entry={e} />
        ))}
      </div>
    </>
  );
}

function Answer({ entry }: { entry: Entry }) {
  const r = entry.result;
  return (
    <Card>
      <CardContent className="grid gap-3">
        <p className="text-sm font-medium">{entry.question}</p>

        {!r && !entry.error && (
          <div className="grid gap-2" aria-busy="true">
            <p className="text-xs text-muted-foreground">SQL을 쓰고 실행하는 중… (보통 5초 정도)</p>
            <Skeleton className="h-5 w-3/4" />
          </div>
        )}

        {entry.error && (
          <p className="flex items-center gap-1.5 text-sm text-destructive">
            <CircleAlert className="size-4" />
            {entry.error}
          </p>
        )}

        {r && (
          <>
            <p className={r.status === "answered" ? "text-base" : "text-base text-muted-foreground"}>{r.answer}</p>

            {r.columns.length > 0 && (
              <div className="max-h-80 overflow-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {r.columns.map((c) => (
                        <TableHead key={c}>{c}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.rows.map((row, i) => (
                      <TableRow key={i}>
                        {row.map((v, j) => (
                          <TableCell key={j} className={typeof v === "number" ? "text-right tabular-nums" : undefined}>
                            {v == null ? "-" : typeof v === "number" ? v.toLocaleString("ko-KR") : String(v)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {r.truncated && <p className="text-xs text-muted-foreground">결과가 많아 앞의 200행만 보입니다.</p>}

            {r.sql && (
              <details className="group text-sm">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
                  <Code className="size-3.5" />
                  실행한 SQL 보기
                  {r.attempts.length > 1 && <Badge variant="outline">{r.attempts.length - 1}번 고쳐서 성공</Badge>}
                  <span className="ml-auto tabular-nums">{r.seconds}초</span>
                </summary>
                {r.attempts.map((a, i) => (
                  <div key={i} className="mt-2">
                    {a.error && <p className="mb-1 text-xs text-destructive">실패: {a.error}</p>}
                    <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                      {a.sql}
                    </pre>
                  </div>
                ))}
              </details>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
