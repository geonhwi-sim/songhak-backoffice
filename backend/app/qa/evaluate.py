"""자연어 질의 평가 세트(eval/questions.yaml) 불러오기와 채점.

사용법 (backend 폴더)
  python -m app.qa.evaluate --gold   정답 SQL이 읽기 전용 계정으로 모두 실행되는지 확인
  python -m app.qa.evaluate --run    에이전트로 20문항을 돌려 채점 (API 비용 발생, 결과는 data/eval_runs/)
"""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import yaml

from app.qa.db import Result, run_sql

QUESTIONS = Path(__file__).resolve().parents[2] / "eval" / "questions.yaml"


@dataclass(frozen=True)
class Question:
    id: str
    area: str
    question: str
    gold_sql: str
    ordered: bool = False
    kind: str = "answer"  # answer | no_data


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    return [Question(**q) for q in yaml.safe_load(path.read_text(encoding="utf-8"))]


def norm(v):
    """비교용 값. 숫자는 소수 둘째 자리까지, 날짜는 문자열로."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        x = round(Decimal(str(v)), 2)
        return int(x) if x == x.to_integral_value() else float(x)
    if isinstance(v, (date, datetime, time)):
        return v.isoformat()
    return str(v).strip()


def is_empty(r: Result) -> bool:
    """행이 없거나, 집계 결과가 모두 NULL이면 '데이터 없음'."""
    return not r.rows or all(v is None for row in r.rows for v in row)


def matches(gold: Result, got: Result, ordered: bool = False) -> bool:
    """행 수가 같고, 정답 각 행의 값이 답 행에 모두 들어 있으면 맞다. 열 이름과 추가 열은 보지 않는다."""
    if is_empty(gold) or is_empty(got):
        return is_empty(gold) and is_empty(got)
    if len(gold.rows) != len(got.rows):
        return False
    want = [Counter(norm(v) for v in row) for row in gold.rows]
    have = [Counter(norm(v) for v in row) for row in got.rows]
    if ordered:
        return all(not (w - h) for w, h in zip(want, have))
    unused = list(range(len(have)))
    for w in want:
        hit = next((i for i in unused if not (w - have[i])), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


def answer_mentions(gold: Result, answer: str) -> bool | None:
    """정답이 숫자 하나일 때, 답변 문장에 그 숫자가 들어 있는지 (SQL이 맞아도 문장이 틀릴 수 있다).
    숫자 하나가 아닌 문항은 None (판단하지 않음)."""
    if len(gold.rows) != 1 or len(gold.rows[0]) != 1 or not isinstance(norm(gold.rows[0][0]), (int, float)):
        return None
    return str(norm(gold.rows[0][0])) in answer.replace(",", "")


def check_gold() -> int:
    failed = 0
    for q in load_questions():
        try:
            r = run_sql(q.gold_sql)
            note = "데이터 없음" if is_empty(r) else f"{len(r.rows)}행"
            if (q.kind == "no_data") != is_empty(r):
                note += "  ← kind와 맞지 않음"
                failed += 1
            print(f"OK   {q.id}  {note}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {q.id}  {str(getattr(e, 'orig', e)).splitlines()[0]}")
    return failed


# Claude Opus 5.5 요금 (USD / 100만 토큰). 캐시 쓰기는 입력의 1.25배, 읽기는 0.20
PRICE = {"input_tokens": 4.0, "output_tokens": 20.0, "cache_creation_input_tokens": 5.0,
         "cache_read_input_tokens": 0.20}
RUNS = Path(__file__).resolve().parents[3] / "data" / "eval_runs"  # 실데이터 답이 들어가므로 gitignore 폴더


def cost(usage: dict) -> float:
    return sum(usage.get(k, 0) * p for k, p in PRICE.items()) / 1_000_000


def run_eval(ids: list[str] | None = None, today: date = date(2026, 11, 5)) -> dict:
    """에이전트에게 평가 세트를 물어 채점한다. 결과 전체는 data/eval_runs/에 저장하고, 화면에는 통과 여부만 보인다."""
    import json

    from app.qa.agent import Agent

    agent = Agent()
    rows = []
    for q in load_questions():
        if ids and q.id not in ids:
            continue
        gold = run_sql(q.gold_sql)
        out = agent.ask(q.question, today)
        got = Result(out["columns"], out["rows"], out["truncated"])
        if q.kind == "no_data":
            ok = out["status"] in ("answered", "no_sql") and is_empty(got)
        else:
            ok = out["status"] == "answered" and matches(gold, got, q.ordered)
        mention = answer_mentions(gold, out["answer"]) if ok and q.kind == "answer" else None
        rows.append({"id": q.id, "area": q.area, "ok": ok, "answer_has_value": mention,
                     "status": out["status"], "tries": len(out["attempts"]),
                     "seconds": out["seconds"], "cost": round(cost(out["usage"]), 4), "question": q.question,
                     "answer": out["answer"], "sql": out["sql"], "attempts": out["attempts"],
                     "gold_rows": [list(map(norm, r)) for r in gold.rows], "rows": [list(map(norm, r)) for r in got.rows]})
        print(f"{'PASS' if ok else 'FAIL'} {q.id}  {out['status']:<8} 시도 {len(out['attempts'])}  "
              f"{out['seconds']:>5}초  ${rows[-1]['cost']:.4f}" + ("  ← 답변 문장에 정답 숫자 없음" if mention is False else ""))

    checked = [r["answer_has_value"] for r in rows if r["answer_has_value"] is not None]
    summary = {"passed": sum(r["ok"] for r in rows), "total": len(rows),
               "answer_has_value": f"{sum(checked)}/{len(checked)}",
               "retried": sum(r["tries"] > 1 for r in rows), "cost": round(sum(r["cost"] for r in rows), 4),
               "seconds": round(sum(r["seconds"] for r in rows), 1), "model": agent.model}
    by_area: dict[str, list[bool]] = {}
    for r in rows:
        by_area.setdefault(r["area"], []).append(r["ok"])
    summary["by_area"] = {a: f"{sum(v)}/{len(v)}" for a, v in by_area.items()}

    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"run_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n정답 {summary['passed']}/{summary['total']}  답변 문장 숫자 {summary['answer_has_value']}  "
          f"영역별 {summary['by_area']}  "
          f"재시도 {summary['retried']}문항  ${summary['cost']:.3f}  {summary['seconds']}초\n저장: {path}")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", action="store_true", help="정답 SQL만 실행해 본다")
    ap.add_argument("--run", action="store_true", help="에이전트로 평가 세트를 돌린다 (API 비용 발생)")
    ap.add_argument("--ids", nargs="*", help="이 문항만 (예: s01 p02)")
    args = ap.parse_args(argv)
    if args.gold:
        raise SystemExit(1 if check_gold() else 0)
    if args.run:
        run_eval(args.ids)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
