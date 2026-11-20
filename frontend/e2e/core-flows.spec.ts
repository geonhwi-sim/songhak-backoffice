import { readFile } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

// 공개 데모(가상 데이터)에서 면접관이 실제로 밟는 흐름을 그대로 확인한다.
// 로그인 → 홈 → 정산서 → 인건비 → 질문하기 → 로그아웃

const toNumber = (text: string) => Number(text.replace(/[^0-9-]/g, ""));

async function demoLogin(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "데모로 둘러보기" }).click();
  await page.waitForURL((url) => url.pathname === "/");
}

test("로그인하지 않으면 로그인 화면으로 보낸다", async ({ page }) => {
  await page.goto("/payroll");
  await expect(page).toHaveURL(/\/login\?next=%2Fpayroll/);
  await expect(page.getByRole("button", { name: "데모로 둘러보기" })).toBeVisible();
});

test("로그인 없이 API를 부르면 401", async ({ request }) => {
  const res = await request.get("/api/settlements?month=2026-09");
  expect(res.status()).toBe(401);
});

test("데모 로그인 후 홈에 동기화·인건비·발주가 보인다 @mobile", async ({ page }) => {
  await demoLogin(page);
  await expect(page.getByText("정산 앱 동기화").first()).toBeVisible();
  await expect(page.getByText("이번 급여 인건비").first()).toBeVisible();
  await expect(page.getByText("최근 발주").first()).toBeVisible();
});

test("정산서 합계가 목록 금액, 항목 금액의 합과 같다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "정산", exact: true }).first().click();
  await expect(page.getByText("정산처별 합계")).toBeVisible();

  const names = (await page.locator("button[aria-pressed]").allInnerTexts()).map((t) => t.trim());
  expect(names.length).toBeGreaterThan(0);

  for (const name of names) {
    const button = page.getByRole("button", { name, exact: true });
    const row = page.locator("tbody tr").filter({ has: button });
    // 금액 칸에는 할인 줄이 붙을 수 있어서 첫 줄만 읽는다
    const listAmount = toNumber((await row.locator("td").nth(2).innerText()).split("\n")[0]);

    await button.click();
    const invoice = page.locator("[data-slot=card]").filter({ hasText: `${name} 정산서` });
    await expect(invoice.getByRole("button", { name: "인쇄" })).toBeVisible();

    const total = toNumber(await invoice.locator("tfoot td").last().innerText());
    const lines = await invoice.locator("tbody tr td:last-child").allInnerTexts();
    const lineSum = lines.reduce((s, t) => s + toNumber(t), 0);

    expect(total, `${name} 정산서 합계 = 목록 금액`).toBe(listAmount);
    expect(lineSum, `${name} 항목 금액 합 = 정산서 합계`).toBe(total);
  }
});

test("정산서를 PDF로 내려받는다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "정산", exact: true }).first().click();
  await page.locator("button[aria-pressed]").first().click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "PDF" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^\d{4}-\d{2} .+ 정산서\.pdf$/);
  const file = await readFile(await download.path());
  expect(file.subarray(0, 5).toString()).toBe("%PDF-");
});

test("정산서: 기간을 직접 골라 다음 달 며칠까지 묶어 본다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "정산", exact: true }).first().click();
  await page.locator("button[aria-pressed]").first().click();
  const invoice = page.locator("[data-slot=card]").filter({ hasText: "정산서" }).last();
  await invoice.getByRole("button", { name: "기간" }).click();

  const start = await invoice.getByLabel("시작일").inputValue(); // 그달 1일
  const [y, m] = start.split("-").map(Number);
  const next = `${m === 12 ? y + 1 : y}-${String((m % 12) + 1).padStart(2, "0")}`;
  // 끝 날짜가 비어 있거나 시작보다 이르면 막는다
  await invoice.getByRole("button", { name: "이 기간으로 보기" }).click();
  await expect(invoice.getByRole("alert")).toContainText("모두 고르세요");

  await invoice.getByLabel("끝 날짜 (포함)").fill(`${next}-02`);
  await invoice.getByRole("button", { name: "이 기간으로 보기" }).click();
  await expect(page.getByTestId("invoice-period")).toContainText(`직접 고른 기간 (${m}/1 ~ ${(m % 12) + 1}/2)`);

  // 합계 = 항목 합, 그리고 API의 한 달 + 다음 달 이틀과 같다
  const card = page.locator("[data-slot=card]").filter({ has: page.getByTestId("invoice-period") });
  const total = toNumber(await card.locator("tfoot td").last().innerText());
  const lines = await card.locator("tbody tr td:last-child").allInnerTexts();
  expect(lines.reduce((s, t) => s + toNumber(t), 0)).toBe(total);

  const pdf = card.getByRole("link", { name: "PDF" });
  const id = (await pdf.getAttribute("href"))!.match(/settlements\/(\d+)\//)![1];
  const get = async (q: string) => (await (await page.request.get(`/api/settlements/${id}?${q}`)).json()).total;
  expect(total).toBe((await get(`month=${start.slice(0, 7)}`)) + (await get(`start=${next}-01&end=${next}-02`)));

  const [download] = await Promise.all([page.waitForEvent("download"), pdf.click()]);
  expect(download.suggestedFilename()).toMatch(new RegExp(`^${start}~${next}-02 .+ 정산서\\.pdf$`));
});

test("인건비 화면에 기본급·주휴수당과 규칙 설명이 보인다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "인건비", exact: true }).first().click();
  await expect(page.locator("thead").getByText("주휴수당", { exact: true })).toBeVisible();
  await expect(page.getByText("일요일이 속한 급여 기간에 한 번")).toBeVisible();
});

test("질문은 2자 이상이어야 보낼 수 있다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "질문하기", exact: true }).first().click();
  const input = page.getByRole("textbox", { name: "질문" });
  const submit = page.getByRole("button", { name: "묻기" });

  await input.fill("가");
  await expect(submit).toBeDisabled();
  await input.fill("   ");
  await expect(submit).toBeDisabled();
  await input.fill("x".repeat(400));
  await expect(input).toHaveValue("x".repeat(300)); // 300자에서 잘린다
  await expect(page.locator("#question-count")).toHaveText("300/300");
});

test("질문하면 답·결과 표·실행한 SQL이 나온다", async ({ page }) => {
  // 실제 AI를 한 번 부른다 (데모 하루 20회 중 1회). E2E_SKIP_ASK=1이면 건너뛴다.
  test.skip(!!process.env.E2E_SKIP_ASK, "E2E_SKIP_ASK");
  test.setTimeout(90_000);

  await demoLogin(page);
  await page.getByRole("link", { name: "질문하기", exact: true }).first().click();
  const quota = page.getByText(/오늘 남은 질문 \d+\/\d+/);
  await expect(quota).toBeVisible();
  test.skip((await quota.innerText()).startsWith("오늘 남은 질문 0/"), "오늘 데모 질문을 다 씀");

  await page.getByRole("textbox", { name: "질문" }).fill("9월 정산처별 금액 알려줘");
  await page.getByRole("button", { name: "묻기" }).click();

  const sqlToggle = page.getByText("실행한 SQL 보기");
  await expect(sqlToggle).toBeVisible({ timeout: 60_000 });
  // 열 이름은 AI가 정하므로 표가 있고 행이 1개 이상인지만 본다
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await sqlToggle.click();
  await expect(page.locator("pre").first()).toContainText(/select/i);
});

test("발주: 비용 합계가 목록 합과 같고, 누르면 품목이 펼쳐진다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "발주", exact: true }).first().click();
  await expect(page.getByText("발주 목록")).toBeVisible();
  // 지난달은 대기 중인 발주가 거의 없어 결제 금액까지 확인할 수 있다
  await page.getByRole("button", { name: "이전 달" }).click();
  const rows = page.locator("li button[aria-expanded]");
  await expect(rows.first()).toBeVisible();

  const costs = await rows.locator("span.font-medium").allInnerTexts();
  const total = toNumber(await page.locator("[data-slot=card]").filter({ hasText: "비용" }).first().locator("[data-slot=card-title]").innerText());
  expect(costs.reduce((s, t) => s + toNumber(t), 0)).toBe(total);

  await rows.first().click();
  await expect(rows.first()).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("예상 금액")).toBeVisible();
  await expect(page.getByText("품목별 지출 상위 10")).toBeVisible();
});

test("손익: 지출을 넣으면 비용과 손익에 반영되고, 지우면 돌아온다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "손익", exact: true }).first().click();
  const card = (title: string) =>
    page.locator("[data-slot=card]").filter({ has: page.locator("[data-slot=card-description]", { hasText: new RegExp(`^${title}$`) }) }).locator("[data-slot=card-title]");
  await expect(page.getByTestId("profit-value")).toBeVisible();
  const read = async () => ({
    revenue: toNumber(await card("매출").innerText()),
    cost: toNumber(await card("비용").innerText()),
    profit: toNumber(await card("손익").innerText()),
  });
  const before = await read();
  expect(before.profit).toBe(before.revenue - before.cost);

  // 공개 데모를 함께 쓰므로 이름을 겹치지 않게 하고, 끝나면 지운다 (데모 DB는 매일 새로 만든다)
  const vendor = `E2E 확인 ${Date.now()}`;
  const form = page.getByRole("form", { name: "지출 입력" });
  await form.getByLabel("산 곳").fill(vendor);
  await form.getByLabel("분류").selectOption({ label: "기타" });
  await form.getByLabel("금액 (원)").fill("12,345");
  await form.getByRole("button", { name: "추가" }).click();

  const list = page.getByRole("list", { name: "지출 목록" });
  await expect(list.getByText(vendor)).toBeVisible();
  await expect(card("비용")).toHaveText(`${(before.cost + 12345).toLocaleString("ko-KR")}원`);
  await expect(card("손익")).toHaveText(`${(before.profit - 12345).toLocaleString("ko-KR")}원`);

  page.once("dialog", (d) => d.accept());
  await list.getByRole("button", { name: `${vendor} 지출 지우기` }).click();
  await expect(list.getByText(vendor)).toHaveCount(0);
  await expect(card("비용")).toHaveText(`${before.cost.toLocaleString("ko-KR")}원`);
});

test("손익: 잘못된 금액은 막고 이유를 보여 준다", async ({ page }) => {
  await demoLogin(page);
  await page.goto("/profit");
  const form = page.getByRole("form", { name: "지출 입력" });
  await form.getByLabel("산 곳").fill("E2E 금액 확인");
  await form.getByLabel("금액 (원)").fill("0");
  await form.getByRole("button", { name: "추가" }).click();
  await expect(form.getByRole("alert")).toContainText("금액");
});

// 테스트용 POS 엑셀 (가짜 숫자, 2020년 1월): backend 폴더에서 python -m tests.pos_files ../frontend/e2e/fixtures로 만든다
const fixture = (name: string) => path.join(__dirname, "fixtures", name);
const DAILY_FILE = fixture("POS정산내역_202001.xlsx");
const MENU_FILE = fixture("상품ABC분석_202001.xlsx");

test("매출: 데모 지난달은 일별 합계와 메뉴별 합계가 맞는다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "매출", exact: true }).first().click();
  await page.getByRole("button", { name: "이전 달" }).click();
  await expect(page.getByRole("status").filter({ hasText: "합계" })).toContainText("같습니다");
  await expect(page.getByRole("list", { name: "일별 매출" }).getByRole("listitem").first()).toBeVisible();
  await expect(page.getByText("외상 · 손익에서 뺌")).toBeVisible();
});

test("매출: POS 엑셀을 미리 보고 넣으면 매출과 손익에 들어간다", async ({ page }) => {
  await demoLogin(page);
  await page.goto("/sales");
  const file = page.getByLabel("엑셀 파일 (.xlsx)");

  // 공개 데모를 함께 쓰므로 데모 데이터와 겹치지 않는 2020년 1월로 올린다 (다시 돌리면 '바꿔 넣기')
  await file.setInputFiles(DAILY_FILE);
  const preview = page.getByLabel("미리보기");
  await expect(preview).toContainText("POS정산내역");
  await expect(preview).toContainText("2,984,500원");
  await page.getByRole("button", { name: /넣기$/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "넣었습니다" })).toBeVisible();
  await expect(page.getByTestId("pos-net")).toHaveText("2,984,500원"); // 올린 달(2020년 1월)로 옮겨 간다

  await file.setInputFiles(MENU_FILE);
  await expect(preview).toContainText("주문확인용");
  await page.getByRole("button", { name: /넣기$/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "합계" })).toContainText("같습니다 (2,984,500원)");

  // 지난 실행이 남긴 보정이 있으면 지우고 본다 (공개 데모를 함께 쓴다)
  const before = await (await page.request.get("/api/pos/sales?month=2020-01")).json();
  for (const a of before.adjustments) await page.request.delete(`/api/revenue-adjustments/${a.id}`);

  // 손익 매출 = POS 실매출 − 주문확인용 + 외상 정산
  const profit = async () => (await page.request.get("/api/profit?month=2020-01")).json();
  const p = await profit();
  expect(p.revenue).toEqual({ credit: 0, pos: 2984500, pos_credit: 524500, credit_paid_other: null, credit_missing: null });
  expect(p.revenue_total).toBe(2984500 - 524500);

  // 외상 보정: 다른 메뉴로 찍은 외상 결제는 POS 자료가 있는 달에서 빼고, 지우면 돌아온다
  const r = await page.request.post("/api/revenue-adjustments", {
    data: { month: "2020-01", kind: "credit_paid", amount: 100000, note: "E2E 거래처 12월분, 물냉면으로 찍음" },
  });
  expect(r.status()).toBe(201);
  expect((await profit()).revenue_total).toBe(2984500 - 524500 - 100000);
  expect((await page.request.delete(`/api/revenue-adjustments/${(await r.json()).id}`)).status()).toBe(200);
  expect((await profit()).revenue_total).toBe(2984500 - 524500);
});

test("매출: 이달 외상 보정을 화면에서 넣고 지운다", async ({ page }) => {
  await demoLogin(page);
  await page.goto("/sales");
  const month = (await (await page.request.get("/api/home")).json()).today.slice(0, 7);
  const profit = async () => (await (await page.request.get(`/api/profit?month=${month}`)).json()).revenue_total;
  const base = await profit();

  const form = page.getByRole("form", { name: "외상 보정 넣기" });
  await form.getByLabel(/정산 앱에서 지운 외상/).check();
  await form.getByLabel("금액 (원)").fill("12,300");
  await form.getByLabel("메모").fill("E2E 거래처 지운 주문");
  await form.getByRole("button", { name: "보정 추가" }).click();
  const list = page.getByRole("list", { name: "외상 보정 목록" });
  await expect(list).toContainText("E2E 거래처 지운 주문");
  await expect(list).toContainText("+12,300원");
  expect(await profit()).toBe(base + 12300);

  page.once("dialog", (d) => d.accept());
  await list.getByRole("button", { name: "E2E 거래처 지운 주문 보정 지우기" }).click();
  await expect(page.getByRole("list", { name: "외상 보정 목록" })).toHaveCount(0);
  expect(await profit()).toBe(base);
});

test("매출: 외상 보정은 메모가 없으면 넣지 않는다", async ({ page }) => {
  await demoLogin(page);
  await page.goto("/sales");
  const form = page.getByRole("form", { name: "외상 보정 넣기" });
  await form.getByLabel("금액 (원)").fill("5000");
  await form.getByLabel("메모").fill("   ");
  await form.getByRole("button", { name: "보정 추가" }).click();
  await expect(form.locator("p[role=alert]")).toContainText("메모");
});

test("매출: POS 엑셀이 아니면 넣지 않고 이유를 보여 준다", async ({ page }) => {
  await demoLogin(page);
  await page.goto("/sales");
  await page.getByLabel("엑셀 파일 (.xlsx)").setInputFiles({ name: "메모.xlsx", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.locator("p[role=alert]")).toContainText("xlsx");
  await expect(page.getByRole("button", { name: /넣기$/ })).toHaveCount(0);
});

test("동기화: 원본 앱별 마지막 성공과 기록이 보인다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("link", { name: "동기화", exact: true }).first().click();
  await expect(page.getByText(/마지막 성공|성공한 기록 없음/)).toHaveCount(2);
  await expect(page.getByText("최근 기록")).toBeVisible();
});

test("직원 계정은 내 근무만 보고, 사장님 화면 주소로 가도 돌아온다 @mobile", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "직원 화면으로 보기" }).click();
  await page.waitForURL(/\/my$/);
  await expect(page.getByRole("heading", { name: /님의 근무$/ })).toBeVisible();
  await expect(page.getByLabel("급여 합계")).toContainText("원");
  await expect(page.getByText(/근무 기록 \d+일/)).toBeVisible();

  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link")).toHaveText(["내 근무"]);

  await page.goto("/settlements");
  await page.waitForURL(/\/my$/);
  // 화면 우회와 상관없이 API가 막는다
  expect((await page.request.get("/api/settlements?month=2026-10")).status()).toBe(403);
  expect((await page.request.get("/api/payroll?month=2026-11")).status()).toBe(403);
});

test("로그아웃하면 다시 로그인 화면으로 간다", async ({ page }) => {
  await demoLogin(page);
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});
