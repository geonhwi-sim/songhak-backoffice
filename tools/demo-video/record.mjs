// 공개 데모 2분 영상: CDP 화면 프레임(JPEG)을 받아 frames/에 저장하고, 나중에 ffmpeg로 MP4를 만든다.
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = "https://demo.43-202-96-199.sslip.io";
const OUT = path.resolve("frames");
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT);

// 자막 띠, 가짜 커서, 전체 화면 카드 (페이지를 새로 열 때마다 다시 만든다)
const OVERLAY = () => {
  const make = () => {
    if (document.getElementById("__cap")) return;
    const st = document.createElement("style");
    st.textContent = `
      #__cap{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:2147483646;
        background:rgba(28,22,18,.92);color:#fff;font:600 21px/1.45 "IBM Plex Sans KR",sans-serif;
        padding:12px 24px;border-radius:12px;max-width:1040px;text-align:center;opacity:0;
        transition:opacity .35s;pointer-events:none;box-shadow:0 8px 30px rgba(0,0,0,.25)}
      #__cap.on{opacity:1}
      #__cap small{display:block;font-weight:400;font-size:16px;opacity:.8;margin-top:2px}
      #__cur{position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
        background:rgba(194,65,12,.35);border:2px solid #C2410C;z-index:2147483647;pointer-events:none;
        transition:transform .12s}
      #__cur.down{transform:scale(.7)}
      #__card{position:fixed;inset:0;z-index:2147483645;background:#1c1612;color:#f5ede6;display:flex;
        flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center;
        font-family:"IBM Plex Sans KR",sans-serif;opacity:0;transition:opacity .5s;pointer-events:none}
      #__card.on{opacity:1}
      #__card h1{font:400 64px/1.2 "Black Han Sans","IBM Plex Sans KR",sans-serif;margin:0;letter-spacing:1px}
      #__card p{margin:0;font-size:24px;opacity:.85}
      #__card .tag{font-size:18px;opacity:.6;letter-spacing:.5px}
      #__card ul{list-style:none;margin:6px 0 0;padding:0;font-size:26px;line-height:1.9}
      #__card ul b{color:#F0844A}
      #__card .url{font-size:22px;color:#F0844A;margin-top:10px}`;
    document.head.appendChild(st);
    const cap = document.createElement("div"); cap.id = "__cap"; document.body.appendChild(cap);
    const cur = document.createElement("div"); cur.id = "__cur"; document.body.appendChild(cur);
    const card = document.createElement("div"); card.id = "__card"; document.body.appendChild(card);
    const pos = window.__curPos || [640, 360];
    cur.style.left = pos[0] + "px"; cur.style.top = pos[1] + "px";
    addEventListener("mousemove", (e) => { cur.style.left = e.clientX + "px"; cur.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", () => cur.classList.add("down"), true);
    addEventListener("mouseup", () => cur.classList.remove("down"), true);
  };
  if (document.body) make(); else addEventListener("DOMContentLoaded", make);
  window.__cap = (html) => { make(); const c = document.getElementById("__cap");
    if (!html) { c.classList.remove("on"); return; } c.innerHTML = html; c.classList.add("on"); };
  window.__card = (html) => { make(); const c = document.getElementById("__card");
    const cur = document.getElementById("__cur");
    if (!html) { c.classList.remove("on"); cur.style.display = ""; return; }
    c.innerHTML = html; c.classList.add("on"); cur.style.display = "none"; };
};

const b = await chromium.launch({ executablePath: process.env.CHROME ?? "C:/Program Files/Google/Chrome/Application/chrome.exe" });
const ctx = await b.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5, locale: "ko-KR", colorScheme: "light" });
await ctx.addInitScript(OVERLAY);
const page = await ctx.newPage();

// ── 녹화 시작 ──
const cdp = await ctx.newCDPSession(page);
const frames = [];
cdp.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
  const f = path.join(OUT, `f${String(frames.length).padStart(5, "0")}.jpg`);
  fs.writeFileSync(f, Buffer.from(data, "base64"));
  frames.push({ f, t: metadata.timestamp });
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});

const wait = (ms) => page.waitForTimeout(ms);
const cap = async (text, sub, ms = 0) => { await page.evaluate(([t, s]) => window.__cap(t ? t + (s ? `<small>${s}</small>` : "") : ""), [text, sub]); if (ms) await wait(ms); };
const card = (html) => page.evaluate((h) => window.__card(h), html);
let mouse = [640, 360];
async function moveTo(loc, { click = false } = {}) {
  await loc.scrollIntoViewIfNeeded();
  const bx = await loc.boundingBox();
  const x = bx.x + bx.width / 2, y = bx.y + bx.height / 2;
  await page.mouse.move(x, y, { steps: 28 });
  mouse = [x, y];
  await page.evaluate((p) => (window.__curPos = p), mouse);
  if (click) { await wait(250); await page.mouse.down(); await wait(90); await page.mouse.up(); }
}
const scroll = (y) => page.evaluate((y) => window.scrollTo({ top: y, behavior: "smooth" }), y);
const nav = (name) => page.getByRole("link", { name, exact: true }).first();

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });

// 1. 시작 화면
await card(`<h1>송학구이 통합 백오피스</h1>
  <p>식당에서 매일 쓰는 정산 앱·발주 앱의 데이터를 모아<br>월말 정산·인건비·데이터 질문을 한 화면에서</p>
  <div class="tag">FastAPI · PostgreSQL · LangGraph · Claude · Next.js · AWS EC2</div>`);
await wait(5200);
await card("");
await wait(700);

// 2. 데모 로그인
await cap("공개 데모: 숫자와 이름은 모두 가상 데이터", "면접관이 비밀번호 없이 바로 써 볼 수 있게 만든 데모 로그인", 800);
await moveTo(page.getByRole("button", { name: "데모로 둘러보기" }));
await wait(1800);
await moveTo(page.getByRole("button", { name: "데모로 둘러보기" }), { click: true });
await page.waitForURL(`${BASE}/`);
await page.waitForLoadState("networkidle");
await wait(500);

// 3. 홈
await cap("홈: 매일 새벽 5시, 두 앱에서 데이터를 자동으로 가져온다", "읽기 전용 서비스 계정 · cron · 마지막 동기화 시각 표시");
await moveTo(page.getByText("정산 앱 동기화").first());
await wait(3800);
await cap("이번 급여 기간 인건비 · 이번 달 정산 · 최근 발주를 한눈에");
await moveTo(page.getByText("이번 급여 인건비").first());
await wait(1600);
await moveTo(page.getByText("최근 발주").first());
await wait(1400);
await scroll(380);
await wait(3000);
await scroll(0);
await wait(900);

// 4. 정산
await moveTo(nav("정산"), { click: true });
await page.waitForURL(/settlements/);
await page.waitForLoadState("networkidle");
await cap("정산: 거래처별 월 합계", "메뉴 가격 인상·거래처 할인 이력을 날짜별로 반영");
await wait(3200);
const rows = page.locator("button[aria-pressed]");
await moveTo(rows.nth(2), { click: true }); // 할인이 있는 거래처
await page.waitForLoadState("networkidle");
await cap("정산서: 주문 항목별 단가·금액, 그대로 인쇄해 거래처에 보낸다", "실데이터 7~9월 금액이 기존 앱과 1원까지 일치 · 8월은 통장 결제액과도 일치");
await wait(3500);
await scroll(420);
await wait(2600);
await scroll(0);
await wait(700);
await moveTo(page.getByRole("button", { name: "인쇄" }));
await wait(1800);

// 5. 인건비
await moveTo(nav("인건비"), { click: true });
await page.waitForURL(/payroll/);
await page.waitForLoadState("networkidle");
await cap("인건비: 급여 기간(전월 17일 ~ 당월 16일)별 기본급과 주휴수당", "주 15시간 이상이면 그 주의 일요일이 속한 기간에 한 번 넣는다");
await moveTo(page.getByText("주휴수당").first());
await wait(5200);

// 6. 질문하기
await moveTo(nav("질문하기"), { click: true });
await page.waitForURL(/ask/);
await page.waitForLoadState("networkidle");
await cap("질문하기: 한국어로 물으면 AI가 SQL을 쓰고 실행해 답한다", "LangGraph + Claude · 질의용 뷰만 읽는 전용 DB 계정 · 쿼리 5초 제한");
const input = page.getByPlaceholder("예: 9월 정산 합계가 얼마야?");
await moveTo(input, { click: true });
await wait(600);
await page.keyboard.type("9월에 가장 많이 팔린 메뉴 5개는?", { delay: 85 });
await wait(700);
await moveTo(page.locator('button[type="submit"]'), { click: true });
await cap("SQL 작성 → 실행 → 실패하면 오류를 보고 2번까지 고쳐서 재시도", "평가 세트 20문항 20/20 · 문항당 약 5초");
await page.getByText("실행한 SQL 보기").first().waitFor({ timeout: 90000 });
await wait(600);
await cap("답 + 결과 표 + 실제로 실행한 SQL", "숫자를 사람이 직접 검증할 수 있게");
await wait(3600);
await cap("");
await moveTo(page.getByText("실행한 SQL 보기").first(), { click: true });
await wait(700);
await scroll(500);
await page.mouse.move(1180, 120, { steps: 20 });
await wait(4500);

// 7. 마무리
await card(`<h1>송학구이 통합 백오피스</h1>
  <ul>
    <li>실데이터 정산 금액 <b>1원 단위까지 일치</b> (기존 앱 · 통장 결제액)</li>
    <li>AI 질문 평가 <b>20/20</b> · 테스트 <b>110개</b></li>
    <li>EC2 · Docker Compose · Nginx · HTTPS · 매일 자동 동기화</li>
  </ul>
  <div class="url">demo.43-202-96-199.sslip.io</div>`);
await wait(6500);

await cdp.send("Page.stopScreencast");
await wait(300);
await b.close();
fs.writeFileSync("frames.json", JSON.stringify(frames));
console.log("frames", frames.length, "seconds", (frames.at(-1).t - frames[0].t).toFixed(1));
