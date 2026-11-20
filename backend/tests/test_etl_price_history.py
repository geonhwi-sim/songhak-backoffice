"""정산 앱이 남기는 가격 이력(menu.history)과 판매 중지(hidden)를 동기화가 어떻게 읽는지. 가상 데이터, DB 없음."""

from datetime import date

from app.etl.rtdb import build_catalog, convert_items, period_at


def snapshot(menu: dict) -> dict:
    return {
        "discountRate": 10,
        "menus": [menu],
        "places": [{"id": "p1", "name": "가상 정산처", "menuPriceType": {}}],
        "orders": [],
    }


def order(day: str, menu_id: str = "m1", option_id: str | None = None) -> dict:
    it = {"menuId": menu_id, "qty": 1}
    if option_id:
        it |= {"optionId": option_id, "full": True}
    return {"id": f"o-{day}", "date": day, "placeId": "p1", "items": [it]}


def test_app_price_history_keeps_old_orders_at_old_price():
    # 8,000 → (11/1부터) 8,500 → (11/21부터) 9,000
    menu = {"id": "m1", "name": "가상 비빔밥", "price": 9000, "options": [{"id": "o1", "name": "곱빼기", "extraPrice": 2000}],
            "history": [{"price": 8500, "to": "2026-11-21"}, {"price": 8000, "to": "2026-11-01"}]}  # 순서가 섞여 있어도 된다
    cat = build_catalog(snapshot(menu), {})
    assert [convert_items(order(d), cat)[0].unit_price for d in ("2026-10-31", "2026-11-01", "2026-11-20", "2026-11-21")] \
        == [8000, 8500, 8500, 9000]
    assert convert_items(order("2026-10-31", option_id="o1"), cat)[0].unit_price == 10000  # 옵션 붙은 그릇도 그날 기본가
    assert convert_items(order("2026-11-21"), cat)[0].price_source == "history"  # 앱 이력은 추정이 아니다


def test_app_history_after_owner_memory_history():
    # 사장님 기억(price_history.json, 추정) 7/14 전 7,500 → 앱 이력 11/21 전 8,000 → 지금 8,500
    menu = {"id": "m1", "name": "가상 비빔밥", "price": 8500, "history": [{"price": 8000, "to": "2026-11-21"}]}
    cat = build_catalog(snapshot(menu), {"menu_prices": [{"source_menu_id": "m1", "price": 7500, "valid_to": "2026-07-14"}]})
    assert [p.value for p in cat.menu_prices["m1"]] == [7500, 8000, 8500]
    assert period_at(cat.menu_prices["m1"], date(2026, 7, 13)).estimated
    assert not period_at(cat.menu_prices["m1"], date(2026, 7, 14)).estimated
    assert convert_items(order("2026-07-13"), cat)[0].price_source == "estimated"


def test_hidden_menu_and_option_are_not_review_items():
    # 판매 중지는 주문 입력에서만 숨기는 것이라, 지난 주문은 확인 필요가 아니다 (지운 메뉴와 다르다)
    menu = {"id": "m1", "name": "가상 냉면", "price": 7000, "hidden": True,
            "options": [{"id": "o1", "name": "곱빼기", "extraPrice": 2000, "hidden": True}]}
    line = convert_items(order("2026-11-10", option_id="o1"), build_catalog(snapshot(menu), {}))[0]
    assert (line.unit_price, line.needs_review, line.option_name) == (9000, False, "곱빼기")
