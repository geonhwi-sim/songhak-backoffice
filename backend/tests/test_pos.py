"""OK POS 엑셀 올리기: 파일 읽기(합계 대조·형식 검사), 미리보기 → 넣기, 같은 기간 바꾸기, 손익 반영.

파일은 tests/pos_files.py가 실제 POS 파일과 같은 모양으로 만든다 (숫자는 가짜).
개발 DB에 실데이터가 있어도 섞이지 않게 2020년 날짜만 쓴다.
"""

import io
import zipfile
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.api import app, get_session
from app.auth import hash_password
from app.db import engine
from app.demo.seed import seed
from app.pos_import import MAX_UPLOAD, PosFileError, parse
from app.reports import pos_sales, profit
from tests.pos_files import E2E_DAYS, E2E_END, E2E_MENU, E2E_START, daily_rows, menu_rows, xlsx

pytestmark = pytest.mark.skipif(engine is None, reason="DATABASE_URL 없음")
PASSWORD = "owner-password-123"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JAN = "2020-01"
J1, J31 = date(2020, 1, 1), date(2020, 1, 31)


def daily(days=E2E_DAYS, start=J1, end=J31, **kw) -> bytes:
    return xlsx(daily_rows(start, end, days, **kw))


def menu(items=E2E_MENU, start=J1, end=J31, **kw) -> bytes:
    return xlsx(menu_rows(start, end, items, **kw))


@pytest.fixture
def session():
    with engine.connect() as conn:
        tx = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield s
        finally:
            s.close()
            tx.rollback()


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "t" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    emp = m.Employee(name="테스트 직원")
    session.add(emp)
    session.flush()
    session.add_all([m.AppUser(email="owner@test.local", role="owner", password_hash=hash_password(PASSWORD)),
                     m.AppUser(email="staff@test.local", role="staff", employee_id=emp.id,
                               password_hash=hash_password(PASSWORD))])
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "owner@test.local", "password": PASSWORD}).status_code == 200
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def upload(c: TestClient, path: str, data: bytes, name: str = "pos.xlsx", ctype: str = XLSX):
    return c.post(path, content=data, headers={"Content-Type": ctype, "X-File-Name": name})


# ── 파일 읽기 ──


def test_reads_daily_file():
    p = parse(daily())
    assert (p.kind, p.start, p.end) == ("daily", J1, J31)
    # 포스 02의 빈 줄은 건너뛰고, 영업일자는 엑셀 일련번호에서 날짜로
    assert [(x.business_date, x.pos_no, x.net) for x in p.daily] == [
        (date(2020, 1, 2), "01", 800000), (date(2020, 1, 3), "01", 1204500), (date(2020, 1, 4), "01", 980000)]
    assert p.daily[0].gross == 812000 and p.daily[0].discount == 12000 and p.daily[0].cash == 95000
    assert p.daily[0].closed_at.isoformat() == "2020-01-02T22:10:00+09:00"
    assert p.net_total == 2984500 and p.warnings == []


def test_reads_menu_file():
    p = parse(menu())
    assert p.kind == "menu" and [x.name for x in p.menu] == ["물냉면", "소주", "주문확인용"]
    assert p.net_total == 2984500 and p.warnings == []


@pytest.mark.parametrize("data, message", [
    (b"not a zip", "xlsx"),
    (b"\xd0\xcf\x11\xe0" + b"\0" * 100, ".xls"),
    (xlsx([["매출 보고서"], [], ["기간 : 2020-01-01 ~ 2020-01-31"]]), "POS정산내역"),
    (daily(total={"net": 1}), "합계"),  # 줄을 지우거나 고친 파일
    (menu(total={"qty": 1}), "판매수량"),
    (xlsx(daily_rows(J1, J31, E2E_DAYS)[:-1]), "'합계' 줄"),  # 잘린 파일
    (daily(days=[(date(2020, 2, 1), 1000, 0, 0)]), "기간 밖"),
    (daily(days=[(date(2020, 1, 2), 1000, 0, 0), (date(2020, 1, 2), 2000, 0, 0)]), "두 번"),
    (daily(start=J31, end=J1), "시작이 끝보다"),
    (daily(days=[]), "마감한 날이 없"),
    (menu(items=[("식사", "1", "물냉면", 1000, 1), ("식사", "1", "비빔냉면", 1000, 1)]), "두 번"),
    (xlsx([["POS정산내역"], [], ["조회일자 없음"]]), "조회 기간"),
])
def test_rejects_bad_files(data, message):
    with pytest.raises(PosFileError, match=message):
        parse(data)


def test_rejects_missing_column():
    rows = daily_rows(J1, J31, E2E_DAYS)
    rows[4][7] = "순매출"  # 실매출액 머리글이 다르면
    with pytest.raises(PosFileError, match="실매출액"):
        parse(xlsx(rows))


def test_rejects_middle_close():
    rows = daily_rows(J1, J31, E2E_DAYS)
    rows[5][3] = "중간마감"  # 중간마감과 일마감이 같이 있으면 같은 매출을 두 번 셀 수 있다
    with pytest.raises(PosFileError, match="중간마감"):
        parse(xlsx(rows))


def test_finds_columns_by_name_not_position():
    rows = daily_rows(J1, J31, E2E_DAYS)
    for r in rows[3:]:  # 맨 끝 두 열(보증금)을 둘째 열 앞으로 옮겨도 읽는다 (첫 열 No.·합계는 그대로)
        r[:] = (r + [None] * 16)[:16]
        r[:] = [r[0]] + r[14:] + r[1:14]
    assert parse(xlsx(rows)).net_total == 2984500


def test_rejects_zip_bomb():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"<" + b"a" * 50_000_000)
    assert len(buf.getvalue()) < MAX_UPLOAD
    with pytest.raises(PosFileError, match="너무 큽니다"):
        parse(buf.getvalue())


def test_warns_when_menu_spans_two_months():
    p = parse(menu(start=date(2020, 1, 15), end=date(2020, 2, 14)))
    assert any("두 달" in w for w in p.warnings)
    assert not any("품목이 없습니다" in w for w in p.warnings)
    p = parse(menu(items=[("식사", "1", "물냉면", 1000, 1)]))
    assert any("품목이 없습니다" in w for w in p.warnings)


# ── 미리보기 · 넣기 ──


def test_preview_does_not_write(client, session):
    r = upload(client, "/api/pos/preview", daily())
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "daily" and body["days"] == 3 and body["net_total"] == 2984500
    assert body["first"] == "2020-01-02" and body["last"] == "2020-01-04"
    assert body["replaces"] == {"rows": 0, "net_total": 0}
    assert session.scalar(select(func.count()).select_from(m.PosImport)) == 0


def test_import_then_same_period_replaces(client, session):
    r = upload(client, "/api/pos/import", daily(), name="POS%EC%A0%95%EC%82%B0.xlsx")
    assert r.status_code == 201
    imp = session.get(m.PosImport, r.json()["import_id"])
    assert imp.file_name == "POS정산.xlsx" and imp.rows == 3 and imp.imported_by is not None

    # 마감을 고친 뒤 다시 받으면: 미리보기에서 바뀔 자료를 보여 주고, 넣으면 그 기간을 통째로 바꾼다
    fixed = daily(days=[(date(2020, 1, 2), 900000, 0, 0), (date(2020, 1, 3), 100000, 0, 0)])
    pre = upload(client, "/api/pos/preview", fixed).json()
    assert pre["replaces"] == {"rows": 3, "net_total": 2984500}
    upload(client, "/api/pos/import", fixed)
    rows = session.scalars(select(m.PosDailySale).where(m.PosDailySale.business_date.between(J1, J31))).all()
    assert sorted((x.business_date.day, x.net) for x in rows) == [(2, 900000), (3, 100000)]
    assert session.scalar(select(func.count()).select_from(m.PosImport)) == 2  # 올린 기록은 둘 다 남는다


def test_menu_import_removes_overlapping_periods(client, session):
    upload(client, "/api/pos/import", menu(start=J1, end=date(2020, 1, 15)))
    upload(client, "/api/pos/import", menu(start=date(2020, 2, 1), end=date(2020, 2, 29)))
    pre = upload(client, "/api/pos/preview", menu()).json()
    assert pre["replaces"]["periods"] == [{"start": "2020-01-01", "end": "2020-01-15", "rows": 3, "net_total": 2984500}]
    upload(client, "/api/pos/import", menu())
    periods = set(session.execute(select(m.PosMenuSale.period_start, m.PosMenuSale.period_end)).all())
    assert periods == {(J1, J31), (date(2020, 2, 1), date(2020, 2, 29))}


@pytest.mark.parametrize("path", ["/api/pos/preview", "/api/pos/import"])
def test_rejects_bad_upload(client, path):
    r = upload(client, path, b"hello")
    assert r.status_code == 422 and "xlsx" in r.json()["detail"]
    # 폼(multipart·text/plain)으로는 보낼 수 없다
    assert upload(client, path, daily(), ctype="text/plain").status_code == 415
    assert upload(client, path, daily(), ctype="multipart/form-data; boundary=x").status_code == 415
    assert upload(client, path, b"\0" * (MAX_UPLOAD + 1)).status_code == 413
    assert upload(client, path, b"").status_code == 422


def test_owner_only(client):
    client.post("/api/auth/logout")
    assert upload(client, "/api/pos/import", daily()).status_code == 401
    assert client.get("/api/pos/sales", params={"month": JAN}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "staff@test.local", "password": PASSWORD}).status_code == 200
    assert upload(client, "/api/pos/import", daily()).status_code == 403
    assert client.get("/api/pos/sales", params={"month": JAN}).status_code == 403


# ── 매출 화면 · 손익 ──


def credit_order(session, d: date, amount: int) -> None:
    place = session.scalar(select(m.Place).where(m.Place.name == "테스트 정산처"))
    if place is None:
        place = m.Place(name="테스트 정산처")
        session.add(place)
        session.flush()
    session.add(m.SalesOrder(place_id=place.id, order_date=d, items=[m.SalesOrderItem(
        line_no=1, kind="custom", item_name="식대", qty=1, unit_price_normal=amount, unit_price=amount,
        is_discounted=False, price_source="snapshot")]))
    session.flush()


def test_sales_and_profit(client, session):
    credit_order(session, date(2020, 1, 3), 500000)

    # 일별만 올리면: POS 매출은 들어가지만 외상(주문확인용)을 아직 못 빼서 알린다
    upload(client, "/api/pos/import", daily())
    p = client.get("/api/profit", params={"month": JAN}).json()
    assert p["revenue"] == {"credit": 500000, "pos": 2984500, "pos_credit": None, "credit_paid_other": None,
                            "credit_missing": None}
    assert p["revenue_total"] == 3484500 and p["pos_connected"] and p["credit_unchecked"]
    assert p["pos_days"] == 3 and p["pos_last_date"] == "2020-01-04"

    # 메뉴별까지 올리면: 매출 = POS − 주문확인용 + 외상 정산
    upload(client, "/api/pos/import", menu())
    p = client.get("/api/profit", params={"month": JAN}).json()
    assert p["revenue"] == {"credit": 500000, "pos": 2984500, "pos_credit": 524500, "credit_paid_other": None,
                            "credit_missing": None}
    assert p["revenue_total"] == 2984500 - 524500 + 500000 and not p["credit_unchecked"]
    assert p["profit"] == p["revenue_total"] - p["cost_total"]

    s = client.get("/api/pos/sales", params={"month": JAN}).json()
    assert [d["date"] for d in s["days"]] == ["2020-01-02", "2020-01-03", "2020-01-04"]
    assert s["net_total"] == 2984500 and s["cash_total"] == 313000 and s["credit_item"] == 524500
    assert [x["name"] for x in s["menu"]] == ["물냉면", "소주", "주문확인용"]  # 실매출 큰 순
    assert [x["is_credit"] for x in s["menu"]] == [False, False, True]
    assert s["checks"] == [{"start": "2020-01-01", "end": "2020-01-31", "menu_net": 2984500, "daily_net": 2984500,
                            "match": True}]
    assert [x["kind"] for x in s["imports"]][:2] == ["menu", "daily"]


def test_adjustments_change_revenue(client, session):
    # 다른 메뉴로 찍은 외상 결제는 빼고, 정산 앱에서 지운 그달 외상은 더한다. 지우면 돌아온다
    credit_order(session, date(2020, 1, 3), 500000)
    upload(client, "/api/pos/import", daily())
    upload(client, "/api/pos/import", menu())
    base = client.get("/api/profit", params={"month": JAN}).json()["revenue_total"]

    add = lambda kind, amount, note: client.post(  # noqa: E731
        "/api/revenue-adjustments", json={"month": JAN, "kind": kind, "amount": amount, "note": note})
    paid = add("credit_paid", 300000, "  거래처A 12월분, 물냉면으로 찍음 ")
    assert paid.status_code == 201 and paid.json()["memo"] == "거래처A 12월분, 물냉면으로 찍음"
    assert add("credit_missing", 40000, "거래처B 1/30~1/31").status_code == 201

    p = client.get("/api/profit", params={"month": JAN}).json()
    assert p["revenue"]["credit_paid_other"] == 300000 and p["revenue"]["credit_missing"] == 40000
    assert p["revenue_total"] == base - 300000 + 40000
    s = client.get("/api/pos/sales", params={"month": JAN}).json()
    assert [(x["kind"], x["amount"]) for x in s["adjustments"]] == [("credit_paid", 300000), ("credit_missing", 40000)]
    assert client.get("/api/pos/sales", params={"month": "2020-02"}).json()["adjustments"] == []  # 다른 달은 그대로

    assert client.delete(f"/api/revenue-adjustments/{paid.json()['id']}").status_code == 200
    assert client.delete(f"/api/revenue-adjustments/{paid.json()['id']}").status_code == 404
    assert client.get("/api/profit", params={"month": JAN}).json()["revenue_total"] == base + 40000


def test_adjustment_paid_needs_pos(client, session):
    # POS 자료가 없는 달에는 뺄 POS 매출이 없으니 '다른 메뉴로 찍은 외상 결제'는 손익에 쓰지 않는다
    client.post("/api/revenue-adjustments", json={"month": "2019-05", "kind": "credit_paid", "amount": 1000, "note": "x"})
    client.post("/api/revenue-adjustments", json={"month": "2019-05", "kind": "credit_missing", "amount": 700, "note": "y"})
    p = client.get("/api/profit", params={"month": "2019-05"}).json()
    assert p["revenue"]["credit_paid_other"] is None and p["revenue"]["credit_missing"] == 700


@pytest.mark.parametrize("body, message", [
    ({"kind": "credit_paid", "amount": 0, "note": "x"}, "금액"),
    ({"kind": "credit_paid", "amount": "1000", "note": "x"}, "금액"),
    ({"kind": "credit_paid", "amount": 1000, "note": "   "}, "메모"),
    ({"kind": "refund", "amount": 1000, "note": "x"}, "종류"),
    ({"month": "2020-13", "kind": "credit_paid", "amount": 1000, "note": "x"}, "월은"),
    ({"month": "2999-01", "kind": "credit_paid", "amount": 1000, "note": "x"}, "앞으로"),
])
def test_adjustment_validation(client, body, message):
    r = client.post("/api/revenue-adjustments", json={"month": JAN, **body})
    assert r.status_code == 422 and message in r.json()["detail"]


def test_adjustment_owner_only(client):
    client.post("/api/auth/logout")
    body = {"month": JAN, "kind": "credit_paid", "amount": 1000, "note": "x"}
    assert client.post("/api/revenue-adjustments", json=body).status_code == 401
    client.post("/api/auth/login", json={"email": "staff@test.local", "password": PASSWORD})
    assert client.post("/api/revenue-adjustments", json=body).status_code == 403
    assert client.delete("/api/revenue-adjustments/1").status_code == 403


def test_check_finds_mismatch_and_menu_shorter_than_daily(session):
    # 메뉴 자료가 1/1~1/3뿐이면, 1/4 매출의 외상은 못 뺐다고 알리고 대조는 그 기간만 한다
    from app.pos_import import apply

    apply(session, parse(daily()), None, None)
    apply(session, parse(menu(items=[("식사", "1", "물냉면", 2000000, 10)], end=date(2020, 1, 3))), None, None)
    s = pos_sales(session, JAN)
    assert s["checks"] == [{"start": J1, "end": date(2020, 1, 3), "menu_net": 2000000, "daily_net": 2004500,
                            "match": False}]
    p = profit(session, JAN)
    assert p["revenue"]["pos_credit"] == 0 and p["credit_unchecked"]


def test_month_without_pos(session):
    p = profit(session, "2019-03")
    assert p["revenue"]["pos"] is None and not p["pos_connected"] and not p["credit_unchecked"]
    assert pos_sales(session, "2019-03")["days"] == []


def test_db_constraints(session):
    from sqlalchemy.exc import IntegrityError

    imp = m.PosImport(kind="daily", period_start=J1, period_end=J31, rows=0, net_total=0)
    session.add(imp)
    session.flush()
    session.add_all([m.PosDailySale(business_date=J1, pos_no="01", gross=1, discount=0, net=1, cash=0, import_id=imp.id),
                     m.PosDailySale(business_date=J1, pos_no="01", gross=2, discount=0, net=2, cash=0, import_id=imp.id)])
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(m.PosImport(kind="daily", period_start=J31, period_end=J1, rows=0, net_total=0))
    with pytest.raises(IntegrityError):
        session.flush()


# ── 데모 ──


def test_demo_pos_matches_and_stays_put(session):
    seed(session, date(2026, 10, 31))
    oct_days = session.execute(select(m.PosDailySale.business_date, m.PosDailySale.net)
                               .order_by(m.PosDailySale.business_date)).all()
    sep = pos_sales(session, "2026-09")
    # 데모도 두 파일이 같은 판매에서 나와 합계가 맞고, 주문확인용 = 그 달 외상 정산
    assert sep["checks"] and all(c["match"] for c in sep["checks"])
    assert sep["credit_item"] == profit(session, "2026-09")["revenue"]["credit"]
    assert date(2026, 9, 24) not in {d["date"] for d in sep["days"]}  # 휴무일

    seed(session, date(2026, 11, 16))
    after = session.execute(select(m.PosDailySale.business_date, m.PosDailySale.net)
                            .where(m.PosDailySale.business_date <= date(2026, 10, 31))
                            .order_by(m.PosDailySale.business_date)).all()
    assert after[:len(oct_days)] == oct_days[:len(oct_days)]  # 10/30까지 그대로 (10/31 seed는 10/30까지 마감)
    nov = pos_sales(session, "2026-11")
    assert nov["last_date"] == date(2026, 11, 14)  # 오늘(11/16)은 마감 전, 11/15는 일요일
    assert all(c["match"] for c in nov["checks"]) and not profit(session, "2026-11")["credit_unchecked"]
