# 백오피스 화면 (Next.js)

실행 방법과 구조는 저장소 루트의 [README](../README.md#화면-nextjs)를 보세요.

- `src/app/login` 로그인
- `src/app/(app)` 로그인 뒤 화면: 홈, 정산(정산서 인쇄), 인건비, 질문하기
- `src/lib/api.ts` FastAPI 호출과 응답 타입, `src/lib/format.ts` 금액·날짜 형식
- `src/proxy.ts` 로그인 쿠키가 없으면 로그인 화면으로 (실제 권한 검사는 FastAPI)
