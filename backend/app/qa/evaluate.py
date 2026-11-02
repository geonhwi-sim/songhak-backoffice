"""자연어 질의 평가 세트(eval/questions.yaml) 불러오기와 채점.

사용법 (backend 폴더)
  python -m app.qa.evaluate --gold   정답 SQL이 읽기 전용 계정으로 모두 실행되는지 확인
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", action="store_true", help="정답 SQL만 실행해 본다")
    args = ap.parse_args(argv)
    if args.gold:
        raise SystemExit(1 if check_gold() else 0)
    ap.print_help()


if __name__ == "__main__":
    main()
