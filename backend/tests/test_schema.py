"""DB 제약 조건이 잘못된 데이터를 실제로 막는지 확인한다.

로컬 Postgres가 최신 마이그레이션까지 올라가 있어야 한다:
    docker compose up -d db && alembic upgrade head && pytest
각 테스트는 트랜잭션 안에서 실행하고 되돌리므로 DB에 데이터가 남지 않는다.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import engine

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")


@pytest.fixture
def conn():
    with engine.connect() as c:
        tx = c.begin()
        yield c
        tx.rollback()


def run(conn, *sqls):
    for s in sqls:
        conn.execute(text(s))


MENU = "insert into menus(id, name) values (9001, '테스트 메뉴')"
PLACE = "insert into places(id, name) values (9001, '테스트 정산처')"
ORDER = "insert into sales_orders(id, place_id, order_date) values (9001, 9001, '2026-10-19')"
ITEM_COLS = "order_id, line_no, kind, menu_id, item_name, qty, unit_price_normal, unit_price, is_discounted, price_source"


def test_28_tables_exist(conn):
    n = conn.execute(text(
        "select count(*) from information_schema.tables "
        "where table_schema = 'public' and table_name <> 'alembic_version'"
    )).scalar()
    assert n == 28  # 23 + payroll_periods (2026-11-02) + expenses + pos 3개 (2026-11-17)


def test_price_periods_cannot_overlap(conn):
    run(conn, MENU, "insert into menu_prices(menu_id, price, valid_from, valid_to) values (9001, 8000, '2026-07-01', '2026-07-15')")
    with pytest.raises(IntegrityError, match="ex_menu_prices_no_overlap"):
        run(conn, "insert into menu_prices(menu_id, price, valid_from) values (9001, 8500, '2026-07-10')")


def test_consecutive_price_periods_are_allowed(conn):
    # 7월 중순 가격 인상: 끝나는 날 = 다음 기간 시작일 (반열린 구간 [from, to))
    run(conn, MENU,
        "insert into menu_prices(menu_id, price, valid_from, valid_to) values (9001, 8000, '2026-07-01', '2026-07-15')",
        "insert into menu_prices(menu_id, price, valid_from) values (9001, 8500, '2026-07-15')")


def test_discount_type_periods_cannot_overlap_per_place_and_menu(conn):
    run(conn, MENU, PLACE,
        "insert into place_menu_pricing(place_id, menu_id, price_type, valid_from) values (9001, 9001, 'discount', '2026-07-01')")
    with pytest.raises(IntegrityError, match="ex_place_menu_pricing_no_overlap"):
        run(conn, "insert into place_menu_pricing(place_id, menu_id, price_type, valid_from) values (9001, 9001, 'normal', '2026-09-01')")


@pytest.mark.parametrize("label, sql, constraint", [
    ("음수 가격", "insert into menu_prices(menu_id, price, valid_from) values (9001, -1, '2026-07-01')", "price_nonneg"),
    ("끝이 시작보다 이름", "insert into menu_prices(menu_id, price, valid_from, valid_to) values (9001, 1, '2026-07-10', '2026-07-01')", "period"),
    ("할인율 100% 이상", "insert into discount_rates(rate_percent, valid_from) values (100, '2026-07-01')", "rate_range"),
    ("카드 끝자리가 숫자 4자리가 아님", "insert into payment_cards(nickname, last4) values ('카드', '12a4')", "last4_digits"),
    ("카드 전체 번호", "insert into payment_cards(nickname, last4) values ('카드', '1234567812345678')", ""),
])
def test_bad_values_are_rejected(conn, label, sql, constraint):
    run(conn, MENU)
    with pytest.raises(Exception, match=constraint):
        run(conn, sql)


def test_custom_item_cannot_point_to_menu(conn):
    run(conn, MENU, PLACE, ORDER)
    with pytest.raises(IntegrityError, match="custom_has_no_menu"):
        run(conn, f"insert into sales_order_items({ITEM_COLS}) values (9001, 1, 'custom', 9001, 'x', 1, 1, 1, false, 'snapshot')")


def test_amount_is_computed_from_qty_and_unit_price(conn):
    run(conn, PLACE, ORDER,
        f"insert into sales_order_items({ITEM_COLS}) values (9001, 1, 'custom', null, '추가 식대', 2, 15000, 15000, false, 'snapshot')")
    assert conn.execute(text("select amount from sales_order_items where order_id = 9001")).scalar() == 30000


@pytest.mark.parametrize("status, ok", [("pending", True), ("ordered", True), ("done", True), ("arrived", False)])
def test_supply_order_status_values(conn, status, ok):
    sql = ("insert into supply_orders(requester_name, status, created_at) "
           f"values ('직원 A', '{status}', now())")
    if ok:
        run(conn, sql)
    else:
        with pytest.raises(IntegrityError, match="ck_supply_orders_status"):
            run(conn, sql)


def test_work_log_end_must_be_after_start(conn):
    run(conn, "insert into employees(id, name) values (9001, '테스트')")
    with pytest.raises(IntegrityError, match="time_order"):
        run(conn, "insert into work_logs(employee_id, work_date, start_time, end_time) values (9001, '2026-10-19', '18:00', '09:00')")
