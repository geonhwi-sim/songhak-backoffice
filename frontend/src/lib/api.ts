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
  /** 직접 고른 기간이면 null */
  month: string | null;
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

// ── 지출 · 손익 (backend reports.expenses, reports.profit) ──

export type ExpenseCategory = "food" | "supplies" | "utilities" | "rent" | "other";

export type Expense = {
  id: number;
  spent_on: string;
  vendor: string;
  category: ExpenseCategory;
  amount: number;
  memo: string | null;
  source: "manual" | "receipt";
};

export type Expenses = {
  month: string;
  start: string;
  end: string;
  expenses: Expense[];
  by_category: Record<ExpenseCategory, number>;
  total: number;
};

export type Profit = {
  month: string;
  start: string;
  end: string;
  pay_start: string;
  pay_end: string;
  // 매출 = POS 실매출 − POS에 찍은 외상(주문확인용, pos_credit) + 외상 정산 − 다른 메뉴로 찍은 외상 결제 + 정산 앱에서 지운 외상
  revenue: {
    credit: number;
    pos: number | null;
    pos_credit: number | null;
    credit_paid_other: number | null;
    credit_missing: number | null;
  };
  revenue_total: number;
  costs: Record<ExpenseCategory, number> & { supply_app: number; labor: number };
  cost_total: number;
  profit: number;
  expense_count: number;
  pos_connected: boolean;
  pos_days: number;
  pos_last_date: string | null;
  pos_menu_until: string | null;
  /** 일별 POS 자료가 메뉴별 자료보다 뒤까지 있어 그 사이 외상을 못 뺐다 */
  credit_unchecked: boolean;
  adjustments: Adjustment[];
};

/** 손익 매출 보정 (backend models.RevenueAdjustment) */
export type AdjustmentKind = "credit_paid" | "credit_missing";

export type Adjustment = { id: number; kind: AdjustmentKind; amount: number; memo: string; created_at: string };

// ── 홀 매출 (OK POS 엑셀) ──

export type PosKind = "daily" | "menu";

export type PosImport = {
  id: number;
  kind: PosKind;
  start: string;
  end: string;
  file_name: string | null;
  rows: number;
  net_total: number;
  imported_at: string;
};

export type PosMenuItem = { code: string; name: string; category: string | null; net: number; qty: number; is_credit: boolean };

export type PosSales = {
  month: string;
  start: string;
  end: string;
  days: { date: string; net: number; gross: number; discount: number; cash: number }[];
  day_count: number;
  net_total: number;
  cash_total: number;
  last_date: string | null;
  menu: PosMenuItem[];
  menu_net_total: number;
  menu_periods: { start: string; end: string }[];
  menu_until: string | null;
  credit_item: number;
  checks: { start: string; end: string; menu_net: number; daily_net: number; match: boolean }[];
  imports: PosImport[];
  adjustments: Adjustment[];
};

/** 미리보기·넣기 결과 (backend app/pos_import.py summary) */
export type PosPreview = {
  kind: PosKind;
  start: string;
  end: string;
  rows: number;
  net_total: number;
  warnings: string[];
  replaces: { rows: number; net_total: number; periods?: { start: string; end: string; rows: number; net_total: number }[] };
  // daily
  days?: number;
  first?: string;
  last?: string;
  gross_total?: number;
  discount_total?: number;
  cash_total?: number;
  // menu
  qty_total?: number;
  credit_total?: number;
  top?: { name: string; net: number; qty: number }[];
  import_id?: number;
};

export const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** 엑셀 파일을 본문 그대로 보낸다 (backend api.py xlsx_body) */
export const uploadPos = (path: "/api/pos/preview" | "/api/pos/import", file: File) =>
  api<PosPreview>(path, {
    method: "POST",
    body: file,
    headers: { "Content-Type": XLSX_MIME, "X-File-Name": encodeURIComponent(file.name) },
  });
