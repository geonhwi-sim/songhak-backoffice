"use client";

import useSWR from "swr";
import { CircleCheck, TriangleAlert } from "lucide-react";

import { Empty, ErrorBox, Loading, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetcher, type SyncRun } from "@/lib/api";
import { kstTime, num } from "@/lib/format";
import { SYNC_SOURCE, SYNC_STATS } from "@/lib/labels";

// 매일 05:00에 돈다. 하루를 조금 넘겨도(26시간) 정상으로 보고, 그보다 오래되면 숫자가 최신이 아니라고 알린다
const STALE_HOURS = 26;

const hoursAgo = (iso: string) => (Date.now() - Date.parse(iso)) / 3_600_000;
const ago = (iso: string) => {
  const h = hoursAgo(iso);
  return h < 1 ? `${Math.max(1, Math.round(h * 60))}분 전` : h < 48 ? `${Math.round(h)}시간 전` : `${Math.round(h / 24)}일 전`;
};
const took = (r: SyncRun) =>
  r.finished_at ? `${Math.max(0, Math.round((Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000))}초` : "-";

export default function SyncPage() {
  const { data, error, isLoading, mutate } = useSWR<SyncRun[]>("/api/sync-runs?limit=60", fetcher);

  return (
    <>
      <PageHeader
        title="동기화"
        description="매일 05:00, 두 원본 앱(Firebase)에서 읽기 전용 계정으로 가져옵니다. 직전에 DB를 백업하고, 원본은 14일치 보관합니다."
      />

      {error ? (
        <ErrorBox error={error} onRetry={() => mutate()} />
      ) : isLoading || !data ? (
        <Loading rows={5} />
      ) : data.length === 0 ? (
        <Empty>동기화 기록이 아직 없습니다.</Empty>
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {(Object.keys(SYNC_SOURCE) as SyncRun["source"][]).map((src) => (
              <SourceCard key={src} source={src} runs={data.filter((r) => r.source === src)} />
            ))}
          </div>

          <Card>
            <CardHeader>
              <CardTitle>최근 기록</CardTitle>
              <CardDescription>최근 {data.length}번 (원본마다 하루 한 번)</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>시작</TableHead>
                    <TableHead>원본</TableHead>
                    <TableHead>결과</TableHead>
                    <TableHead className="text-right">걸린 시간</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="tabular-nums">{kstTime(r.started_at)}</TableCell>
                      <TableCell>{SYNC_SOURCE[r.source]}</TableCell>
                      <TableCell>
                        <StatusBadge status={r.status} />
                        {r.error && <div className="mt-1 max-w-md text-xs break-words text-destructive">{r.error}</div>}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{took(r)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}

function SourceCard({ source, runs }: { source: SyncRun["source"]; runs: SyncRun[] }) {
  const latest = runs[0];
  const ok = runs.find((r) => r.status === "succeeded");
  const stale = !ok || hoursAgo(ok.started_at) > STALE_HOURS;
  const failedLast = latest && latest.status !== "succeeded";
  const warn = stale || failedLast;

  return (
    <Card className={warn ? "border-amber-300" : undefined}>
      <CardHeader>
        <CardDescription className="flex items-center gap-1.5">
          {warn ? <TriangleAlert className="size-4 text-amber-600" /> : <CircleCheck className="size-4 text-emerald-600" />}
          {SYNC_SOURCE[source]}
        </CardDescription>
        <CardTitle className="text-xl">
          {ok ? `마지막 성공 ${ago(ok.started_at)}` : "성공한 기록 없음"}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {ok && <p className="text-xs text-muted-foreground">{kstTime(ok.started_at)}에 시작, {took(ok)} 걸림</p>}
        {warn && (
          <p className="rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
            {failedLast ? "마지막 실행이 실패했습니다. " : ""}
            {stale ? `${STALE_HOURS}시간 넘게 성공하지 못해 화면의 숫자가 최신이 아닐 수 있습니다.` : ""}
          </p>
        )}
        {ok?.stats && (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
            {SYNC_STATS[source]
              .filter(([key]) => ok.stats?.[key] != null)
              .map(([key, label]) => (
                <div key={key} className="flex justify-between gap-2">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="tabular-nums">{num(ok.stats![key])}</dd>
                </div>
              ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "succeeded") return <Badge variant="outline">성공</Badge>;
  if (status === "running") return <Badge variant="secondary">실행 중</Badge>;
  return <Badge variant="destructive">실패</Badge>;
}
