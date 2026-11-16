import { readFile } from "node:fs/promises";

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
