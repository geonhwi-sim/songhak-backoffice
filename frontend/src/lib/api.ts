// FastAPI 호출. 응답 타입은 backend/app/reports.py, api.py와 맞춘다.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    // 로그인이 풀렸으면 로그인 화면으로 (지금 보던 화면으로 돌아오게). 컴포넌트 밖이라 라우터를 쓸 수 없다.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new ApiError(401, "로그인이 필요합니다");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "요청을 처리하지 못했습니다";
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const fetcher = <T,>(path: string) => api<T>(path);

// ── 응답 타입 ──

export type Me = { id: number; email: string; role: "owner" | "staff" };

export type PlaceTotal = {
  place_id: number;
  name: string;
  orders: number;
  amount: number;
  discount: number;
  estimated_items: number;
  review_items: number;
};

export type Settlements = { month: string; start: string; end: string; places: PlaceTotal[]; total: number };

export type SettlementItem = {
  name: string;
  option: string | null;
  kind: string;
  qty: number;
  unit_price: number;
  amount: number;
  is_discounted: boolean;
  price_source: "snapshot" | "history" | "estimated";
  needs_review: boolean;
};

export type SettlementDetail = {
  place_id: number;
  name: string;
  month: string;
  start: string;
  end: string;
  orders: { order_id: number; date: string; items: SettlementItem[]; total: number }[];
  total: number;
};

export type EmployeeWage = {
  employee_id: number;
  name: string;
  days: number;
  hours: number;
  base_pay: number;
  juhu_weeks: number;
  juhu_pay: number;
  total: number;
};

export type Payroll = { month: string; start: string; end: string; employees: EmployeeWage[]; total: number };

// 직원 본인 화면 (/api/me/pay)
export type WorkLogRow = {
  date: string;
  start: string;
  end: string;
  break_min: number;
  hours: number;
  hourly_rate: number;
  pay: number;
};
export type MyPay = {
  name: string;
  month: string;
  start: string;
  end: string;
  is_estimate: boolean;
  days: number;
  hours: number;
  base_pay: number;
  juhu_weeks: number;
  juhu_pay: number;
  total: number;
  logs: WorkLogRow[];
};

export type SupplyOrder = {
  id: number;
  created_at: string;
  requester: string;
  status: "pending" | "ordered" | "done";
  memo: string | null;
  estimated_total: number | null;
  paid_amount: number | null;
  cost: number;
  paid_on: string | null;
  card_label: string | null;
  source_deleted: boolean;
  items: { name: string; unit: string | null; quantity: number; unit_price: number | null; subtotal: number | null }[];
};

export type SyncRun = {
  id: number;
  source: "rtdb" | "firestore";
  status: string;
  started_at: string;
  finished_at: string | null;
  stats?: Record<string, number> | null;
  error: string | null;
};

export type SupplyOrders = { orders: SupplyOrder[]; count: number; cost: number };

export type Home = {
  today: string;
  payroll: Payroll;
  settlements: Settlements;
  supply_orders: { orders: SupplyOrder[]; count: number; cost: number };
  last_sync: Partial<Record<"rtdb" | "firestore", SyncRun>>;
};

export type AskResult = {
  question: string;
  status: "answered" | "no_sql" | "failed" | "refused";
  answer: string;
  sql: string | null;
  attempts: { sql: string; error: string | null }[];
  columns: string[];
  rows: (string | number | boolean | null)[][];
  truncated: boolean;
  seconds: number;
  remaining_today?: number; // 하루 질문 제한이 있는 서버(데모)에서만
};

export type AskQuota = { limit: number | null; remaining: number | null };

// 데모 서버용 빌드에서만 "1" (Dockerfile 빌드 인자). 빌드할 때 코드에 박힌다.
export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "1";
