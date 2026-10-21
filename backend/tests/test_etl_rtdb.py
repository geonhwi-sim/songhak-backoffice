"""정산 앱 스냅샷 가져오기 검증.

실데이터와 기준표는 공개 저장소에 올리지 않으므로(data/, gitignore) 파일이 있을 때만 돈다.
- data/rtdb_songhak_data_*.json : 정산 앱 스냅샷 (가장 최근 파일)
- data/price_history.json       : 사장님이 알려 준 과거 가격 이력
- data/검증기준_정산.csv         : 정산 앱 화면 금액(앱_금액)과 이력을 반영한 추정액(실청구_추정)
DB 테스트는 트랜잭션 안에서 실행하고 되돌린다.
"""

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import models as m
from app.db import engine
from app.etl.rtdb import build_catalog, convert_items, import_snapshot, to_list

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOTS = sorted(DATA.glob("rtdb_songhak_data_*.json"))
HISTORY = DATA / "price_history.json"
EXPECTED = DATA / "검증기준_정산.csv"

pytestmark = pytest.mark.skipif(not SNAPSHOTS, reason="정산 앱 스냅샷 없음 (data/)")
needs_db = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")
needs_expected = pytest.mark.skipif(not EXPECTED.exists(), reason="검증 기준표 없음 (data/)")


@pytest.fixture(scope="module")
def data():
    return json.loads(SNAPSHOTS[-1].read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def history():
    return json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}


@pytest.fixture
def session():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield s
        s.close()
        tx.rollback()


def expected(column: str) -> dict[tuple[str, str], int]:
    """기준표에서 {(월, 정산처): 금액}. 합계 줄은 뺀다."""
    with EXPECTED.open(encoding="utf-8-sig") as f:
        return {(r["월"], r["정산처"]): int(r[column]) for r in csv.DictReader(f)
                if r[column] and not r["정산처"].startswith("(")}


def month_totals(session: Session, month: str) -> dict[tuple[str, str], int]:
    y, mo = map(int, month.split("-"))
    start, end = date(y, mo, 1), date(y + mo // 12, mo % 12 + 1, 1)
    rows = session.execute(
        select(m.Place.name, func.sum(m.SalesOrderItem.amount))
        .join(m.SalesOrder, m.SalesOrder.id == m.SalesOrderItem.order_id)
        .join(m.Place, m.Place.id == m.SalesOrder.place_id)
        .where(m.SalesOrder.order_date >= start, m.SalesOrder.order_date < end,
               m.SalesOrder.source_deleted_at.is_(None))
        .group_by(m.Place.name)
    ).all()
    return {(month, name): int(total) for name, total in rows}


# ── DB 없이: 변환 규칙 ──


def test_merging_legacy_options_keeps_every_order_total(data, history):
    cat = build_catalog(data, history)
    for o in to_list(data["orders"]):
        merged = sum(ln.amount for ln in convert_items(o, cat, merge=True))
        assert merged == sum(ln.amount for ln in convert_items(o, cat, merge=False)), o["id"]


def test_only_deleted_menus_and_options_need_review(data, history):
    cat = build_catalog(data, history)
    deleted_opts = {h["source_option_id"] for h in history.get("deleted_options", [])}
    deleted_menus = {h["source_menu_id"] for h in history.get("deleted_menus", [])}
    want = sum(
        it["qty"] for o in to_list(data["orders"]) for it in to_list(o["items"])
        if it.get("optionId") in deleted_opts or it.get("menuId") in deleted_menus
    )
    lines = [ln for o in to_list(data["orders"]) for ln in convert_items(o, cat)]
    assert sum(ln.qty for ln in lines if ln.needs_review) == want
    assert all(ln.price_source == "estimated" for ln in lines if ln.needs_review)


# ── DB: 가져오기 ──


@needs_db
def test_import_twice_keeps_row_counts(session, data, history):
    tables = ("places", "menus", "menu_options", "menu_prices", "place_menu_pricing",
              "sales_orders", "sales_order_items", "employees", "work_logs", "day_memos")

    def counts():
        return {t: session.execute(text(f"select count(*) from {t}")).scalar() for t in tables}

    first = import_snapshot(session, data, history)
    before = counts()
    import_snapshot(session, data, history)
    assert counts() == before
    assert first["orders"] == len(to_list(data["orders"]))


@needs_db
@needs_expected
def test_september_matches_app_screen(session, data, history):
    """9월은 가격 이력·지운 항목의 영향이 없는 달이라, 변환 로직만 순수하게 검증된다."""
    import_snapshot(session, data, history)
    want = {k: v for k, v in expected("앱_금액").items() if k[0] == "2026-09"}
    assert month_totals(session, "2026-09") == want


@needs_db
@needs_expected
@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09"])
def test_months_match_estimated_billing(session, data, history, month):
    import_snapshot(session, data, history)
    want = {k: v for k, v in expected("실청구_추정").items() if k[0] == month}
    assert month_totals(session, month) == want
