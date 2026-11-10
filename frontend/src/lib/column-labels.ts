// 자연어 질의 결과 표의 컬럼 이름(SQL 별칭)을 한글 제목으로 바꾼다.
// qa 뷰의 컬럼은 그대로 사전에서 찾고, AI가 붙이는 별칭(total_amount, order_count 등)은
// 앞말·뒷말을 나눠 조합한다. 모르는 이름은 원래 이름을 그대로 보여 준다.

const WORDS: Record<string, string> = {
  // 정산
  place_id: "정산처 ID",
  place_name: "정산처",
  name: "이름",
  is_active: "사용 중",
  order_id: "주문 ID",
  item_id: "항목 ID",
  order_date: "날짜",
  kind: "종류",
  item_name: "항목",
  option_name: "옵션",
  qty: "수량",
  quantity: "수량",
  unit_price_normal: "할인 전 단가",
  unit_price: "단가",
  amount: "금액",
  discount_amount: "할인액",
  discount: "할인액",
  is_discounted: "할인 여부",
  price_source: "가격 출처",
  needs_review: "확인 필요",
  sales: "매출",
  // 인건비
  employee_id: "직원 ID",
  employee_name: "직원",
  work_date: "근무일",
  start_time: "출근",
  end_time: "퇴근",
  break_min: "휴게(분)",
  hours: "근무시간",
  month: "급여 월",
  period_start: "시작일",
  period_end: "종료일",
  days: "근무일수",
  base_pay: "기본급",
  juhu_weeks: "주휴 주수",
  juhu_pay: "주휴수당",
  total: "총액",
  pay: "급여",
  // 발주
  created_at_kst: "발주 시각",
  requester_name: "요청자",
  status: "상태",
  memo: "메모",
  estimated_total: "예상 금액",
  paid_amount: "결제 금액",
  paid_on: "결제일",
  card_label: "카드",
  cost: "비용",
  removed_from_app: "앱에서 지워짐",
  category: "분류",
  unit: "단위",
  subtotal: "소계",
  // 집계에 자주 쓰는 말
  count: "건수",
  cnt: "건수",
  order: "주문",
  orders: "주문",
  item: "항목",
  items: "항목",
  place: "정산처",
  employee: "직원",
  menu: "메뉴",
  date: "날짜",
  day: "날짜",
  week: "주",
  year: "연도",
  avg: "평균",
  average: "평균",
  max: "최대",
  min: "최소",
  sum: "합계",
};

// 앞에 붙는 집계어: total_amount → 총 금액
const PREFIX: Record<string, string> = {
  total: "총",
  sum: "합계",
  avg: "평균",
  average: "평균",
  max: "최대",
  min: "최소",
};

// 뒤에 붙는 집계어: order_count → 주문 건수
const SUFFIX: Record<string, string> = {
  count: "건수",
  cnt: "건수",
  total: "합계",
  sum: "합계",
  avg: "평균",
};

export function columnLabel(column: string): string {
  const key = column.toLowerCase();
  if (WORDS[key]) return WORDS[key];

  const parts = key.split("_");
  if (parts.length < 2) return column;

  const [first, ...restParts] = parts;
  const rest = restParts.join("_");
  if (PREFIX[first] && WORDS[rest]) {
    // total_amount, total_sales → "총액", "총매출"처럼 붙여 쓴다
    return first === "total" ? `총${WORDS[rest] === "금액" ? "액" : WORDS[rest]}` : `${PREFIX[first]} ${WORDS[rest]}`;
  }

  const last = parts[parts.length - 1];
  const head = parts.slice(0, -1).join("_");
  if (SUFFIX[last] && WORDS[head]) return `${WORDS[head]} ${SUFFIX[last]}`;

  return column;
}
