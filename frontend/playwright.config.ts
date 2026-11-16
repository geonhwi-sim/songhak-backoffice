import { defineConfig, devices } from "@playwright/test";

// 핵심 흐름 E2E. 기본 대상은 공개 데모 서버이고, E2E_BASE_URL로 바꿀 수 있다.
// 브라우저는 내려받지 않고 PC에 설치된 Chrome을 쓴다.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://demo.43-202-96-199.sslip.io",
    locale: "ko-KR",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "mobile", use: { ...devices["Pixel 7"], channel: "chrome" }, grep: /@mobile/ },
  ],
});
