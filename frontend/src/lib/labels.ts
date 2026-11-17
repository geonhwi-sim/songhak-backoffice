// 여러 화면이 같이 쓰는 표시 이름

import type { ExpenseCategory, SupplyOrder, SyncRun } from "@/lib/api";

export const SUPPLY_STATUS: Record<SupplyOrder["status"], { label: string; variant: "default" | "secondary" | "outline" }> = {
  pending: { label: "대기", variant: "default" },
  ordered: { label: "주문함", variant: "secondary" },
  done: { label: "완료", variant: "outline" },
};

export const SYNC_SOURCE: Record<SyncRun["source"], string> = { rtdb: "정산 앱", firestore: "발주 앱" };

// 동기화 결과(sync_runs.stats)에서 사람이 볼 만한 숫자만. 키는 backend/app/etl/rtdb.py, firestore.py
export const SYNC_STATS: Record<SyncRun["source"], [key: string, label: string][]> = {
  rtdb: [
    ["orders", "주문"],
    ["items", "주문 항목"],
    ["work_logs", "근무 기록"],
    ["items_needs_review", "확인 필요 항목"],
  ],
  firestore: [
    ["orders", "발주"],
    ["order_items", "발주 품목"],
    ["payments", "결제"],
    ["order_items_unmatched", "단가표에 없는 품목"],
  ],
};

// 지출 분류. 순서 = 입력 화면의 선택지 순서. 값은 backend models.EXPENSE_CATEGORIES
export const EXPENSE_CATEGORY: Record<ExpenseCategory, string> = {
  food: "식재료",
  supplies: "소모품",
  utilities: "공과금",
  rent: "임대료",
  other: "기타",
};
