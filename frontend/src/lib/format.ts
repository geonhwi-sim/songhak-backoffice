// 화면 표시용 형식. 날짜는 한국 시간 기준.

const nf = new Intl.NumberFormat("ko-KR");

export const won = (n: number | null | undefined) => (n == null ? "-" : `${nf.format(n)}원`);
export const num = (n: number | null | undefined) => (n == null ? "-" : nf.format(n));
export const hours = (h: number) => `${nf.format(Math.round(h * 100) / 100)}시간`;

/** "2026-10-17" → "10/17" */
export const md = (d: string) => {
  const [, m, day] = d.slice(0, 10).split("-");
  return `${Number(m)}/${Number(day)}`;
};

/** ISO 시각 → "10/19 13:11" (KST) */
export const kstTime = (iso: string) => {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Seoul",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(iso))
      .map((x) => [x.type, x.value]),
  );
  return `${p.month}/${p.day} ${p.hour}:${p.minute}`;
};

/** 오늘(KST) "YYYY-MM-DD" */
export const todayKst = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());

/** "2026-11" → "2026년 11월" */
export const monthLabel = (m: string) => `${m.slice(0, 4)}년 ${Number(m.slice(5, 7))}월`;

export const shiftMonth = (m: string, delta: number) => {
  const d = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)) - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};

/** 정산서는 매달 1일에 지난달 것을 인쇄하므로, 기본으로 지난달을 연다 */
export const lastMonth = () => shiftMonth(todayKst().slice(0, 7), -1);

/** 급여 기간 이름. 17일부터는 다음 달 급여 (backend/app/reports.py current_pay_month와 같음) */
export const currentPayMonth = () => {
  const t = todayKst();
  return Number(t.slice(8, 10)) >= 17 ? shiftMonth(t.slice(0, 7), 1) : t.slice(0, 7);
};
