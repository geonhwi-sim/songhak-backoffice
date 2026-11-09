"""데모 DB용 가짜 데이터. 실제 가게·거래처·직원·금액과 관계없다.

사용법 (backend 폴더, 데모 DB를 가리키는 DATABASE_URL로)
  python -m app.demo.seed                   7/1 ~ 오늘
  python -m app.demo.seed --until 2026-11-05

- DB 이름이 _demo로 끝날 때만 실행한다. 모든 데이터를 지우고 다시 만들기 때문이다.
- 난수 시드가 고정이라 같은 --until이면 항상 같은 데이터가 나온다 (평가 결과를 비교할 수 있게).
  정산·직원별 근무·발주가 각자 난수를 쓰고 날짜순으로 뽑으므로, --until을 늘려도 지난 날짜는 그대로이고
  새 날짜만 붙는다. 그래서 데모 서버는 매일 새로 만들어도 어제까지의 숫자가 바뀌지 않는다.
- 운영 데이터와 같은 모양을 일부러 넣었다: 7/14 가격 인상, 할인 정산처, 옵션(곱빼기), 직접 입력 금액,
  원본에서 지운 주문, 결제 정보가 없는 발주, 주 15시간 미만 근무(주휴수당 없음), 시급 인상, 공휴일 휴무.
"""

import argparse
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app import models as m
from app.db import Base, SessionLocal
from app.demo import DEMO_EMAIL
from app.etl.rtdb import disc
from app.payroll import refresh_periods

KST = timezone(timedelta(hours=9))
START = date(2026, 7, 1)
PRICE_UP = date(2026, 7, 14)  # 냉면류 가격 인상일
SEED = 2026
DISCOUNT_RATE = Decimal(10)

# 가게가 쉬는 날 (광복절 대체, 추석, 개천절 대체, 한글날)
HOLIDAYS = {date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 5), date(2026, 10, 9)}

# (이름, 분류, 7/14 전 가격, 7/14부터 가격, 주문 가중치)
MENUS = [
    ("육회비빔밥", "식사", 9000, 9000, 9),
    ("비빔밥", "식사", 8000, 8000, 6),
    ("물냉면", "냉면", 7000, 8000, 10),
    ("비빔냉면", "냉면", 7500, 8500, 7),
    ("갈비탕", "식사", 11000, 11000, 5),
    ("된장찌개", "식사", 8000, 8000, 6),
    ("김치찌개", "식사", 8000, 8000, 6),
    ("제육볶음", "식사", 9000, 9000, 5),
    ("돼지불고기 정식", "정식", 12000, 12000, 3),
    ("떡갈비 정식", "정식", 13000, 13000, 2),
]
# (메뉴, 옵션 이름, 추가금)
OPTIONS = [
    ("육회비빔밥", "곱빼기", 2000),
    ("비빔밥", "곱빼기", 1500),
    ("물냉면", "곱빼기", 2000),
    ("비빔냉면", "곱빼기", 2000),
    ("물냉면", "사리 추가", 2000),
]
# (이름, 평일 주문 확률, 한 번에 시키는 그릇 수 범위, 토요일도 시키는지)
PLACES = [
    ("가람물류", 0.85, (6, 14), False),
    ("한결건설 현장", 0.75, (8, 20), True),
    ("누리정밀", 0.80, (4, 10), False),
    ("새솔요양병원", 0.55, (3, 8), False),
    ("바른택배 터미널", 0.45, (5, 12), True),
]
DISCOUNT_PLACE = "누리정밀"
DISCOUNT_EXCEPT = {"육회비빔밥", "물냉면"}  # 할인 정산처라도 기본가로 받는 메뉴

# (분류, 이름, 단위, 단가, 한 번에 시키는 수량 범위)
SUPPLIES = [
    ("육류", "한우 우둔(육회용)", "kg", 52000, (2, 6)),
    ("육류", "돼지 앞다리", "kg", 12000, (3, 10)),
    ("육류", "소 사골", "kg", 9000, (3, 8)),
    ("채소", "배", "박스", 45000, (1, 2)),
    ("채소", "오이", "박스", 28000, (1, 2)),
    ("채소", "무", "개", 2500, (5, 15)),
    ("채소", "대파", "단", 3500, (3, 8)),
    ("채소", "양파", "망", 15000, (1, 3)),
    ("채소", "상추", "kg", 9000, (1, 4)),
    ("면·곡류", "냉면 사리", "박스", 32000, (1, 3)),
    ("면·곡류", "쌀 20kg", "포대", 62000, (1, 2)),
    ("양념", "고추장", "통", 28000, (1, 2)),
    ("양념", "참기름", "병", 18000, (1, 3)),
    ("양념", "식초", "통", 9000, (1, 2)),
    ("양념", "겨자", "통", 7000, (1, 2)),
    ("소모품", "위생장갑", "박스", 12000, (1, 3)),
    ("소모품", "일회용 용기", "박스", 38000, (1, 3)),
    ("소모품", "키친타월", "묶음", 15000, (1, 2)),
    ("소모품", "주방 세제", "통", 22000, (1, 1)),
]
CARDS = [("사업자 카드", "4821"), ("법인 체크카드", "1937")]


@dataclass
class Staff:
    name: str
    start: date
    rates: list[tuple[int, date, date | None]]  # (시급, 시작, 끝)
    weekdays: tuple[int, ...]  # 0=월
    hours: tuple[time, time]
    extra_day: float  # 정해진 요일 말고 하루 더 나올 확률 (주마다)


STAFF = [
    Staff("김하늘", START, [(11000, START, None)], (0, 1, 2, 3, 4), (time(10), time(15)), 0.15),
    # 3일 근무(주 12시간)는 주휴수당이 없고, 하루 더 나온 주(16시간)만 생긴다
    Staff("박도윤", date(2026, 9, 1), [(10500, date(2026, 9, 1), date(2026, 10, 17)), (11000, date(2026, 10, 17), None)],
          (0, 2, 4), (time(11), time(15)), 0.35),
]
REQUESTERS = ["사장님", "김하늘", "박도윤"]
MEMOS = {date(2026, 8, 28): "한결건설 현장 회식 단체 주문", date(2026, 10, 2): "연휴 전날, 재료 넉넉히",
         date(2026, 11, 3): "냉면 사리 거래처 변경 검토"}


def days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def is_open(d: date) -> bool:
    return d.weekday() < 6 and d not in HOLIDAYS  # 일요일 휴무


def at(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=KST)


def wipe(s: Session) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def seed(s: Session, until: date) -> dict:
    rng = random.Random(f"{SEED}-sales")
    wipe(s)

    # ── 정산: 메뉴·가격·정산처 ──
    cats = {}
    for i, name in enumerate(dict.fromkeys(c for _, c, *_ in MENUS)):
        cats[name] = m.MenuCategory(name=name, sort_order=i)
    s.add_all(cats.values())
    s.flush()
    menus = {}
    for name, cat, before, after, _ in MENUS:
        menus[name] = m.Menu(name=name, category_id=cats[cat].id)
    s.add_all(menus.values())
    s.flush()
    prices = []
    for name, _, before, after, _ in MENUS:
        if before == after:
            prices.append(m.MenuPrice(menu_id=menus[name].id, price=after, valid_from=START, valid_to=None))
        else:
            prices += [m.MenuPrice(menu_id=menus[name].id, price=before, valid_from=START, valid_to=PRICE_UP),
                       m.MenuPrice(menu_id=menus[name].id, price=after, valid_from=PRICE_UP, valid_to=None)]
    options = {}
    for menu, opt, _ in OPTIONS:
        options[(menu, opt)] = m.MenuOption(menu_id=menus[menu].id, name=opt)
    s.add_all(prices + list(options.values()))
    s.flush()
    s.add_all(m.MenuOptionPrice(option_id=options[(mn, o)].id, extra_price=x, valid_from=START, valid_to=None)
              for mn, o, x in OPTIONS)
    s.add(m.DiscountRate(rate_percent=DISCOUNT_RATE, valid_from=START, valid_to=None))

    places = {name: m.Place(name=name) for name, *_ in PLACES}
    s.add_all(places.values())
    s.flush()
    s.add_all(m.PlaceMenuPricing(place_id=places[DISCOUNT_PLACE].id, menu_id=menus[mn].id, valid_from=START,
                                 valid_to=None, price_type="normal" if mn in DISCOUNT_EXCEPT else "discount")
              for mn, *_ in MENUS)

    def price_at(menu: str, d: date) -> int:
        _, _, before, after, _ = next(x for x in MENUS if x[0] == menu)
        return before if d < PRICE_UP else after

    names = [x[0] for x in MENUS]
    weights = [x[4] for x in MENUS]
    opts_by_menu: dict[str, list[tuple[str, int]]] = {}
    for mn, o, x in OPTIONS:
        opts_by_menu.setdefault(mn, []).append((o, x))

    orders = deleted = 0
    for d in days(START, until):
        if not is_open(d):
            continue
        for pname, prob, (lo, hi), saturday in PLACES:
            if d.weekday() == 5 and not saturday:
                continue
            if rng.random() > (prob * (0.6 if d.weekday() == 5 else 1)):
                continue
            place = places[pname]
            bowls = rng.randint(lo, hi) * (3 if MEMOS.get(d, "").startswith(pname) else 1)
            picked: dict[str, int] = {}
            for _ in range(bowls):
                mn = rng.choices(names, weights)[0]
                picked[mn] = picked.get(mn, 0) + 1
            lines = []
            for mn, qty in picked.items():
                base = price_at(mn, d)
                # 일부 그릇은 옵션으로 (냉면은 여름에 곱빼기가 많다)
                n_opt = 0
                if mn in opts_by_menu and qty > 1 and rng.random() < (0.45 if d.month in (7, 8) else 0.25):
                    n_opt = rng.randint(1, max(1, qty // 3))
                discounted = pname == DISCOUNT_PLACE and mn not in DISCOUNT_EXCEPT
                if qty - n_opt > 0:
                    lines.append(dict(kind="menu", menu_id=menus[mn].id, option_id=None, item_name=mn,
                                      option_name=None, qty=qty - n_opt, unit_price_normal=base,
                                      unit_price=disc(base, DISCOUNT_RATE) if discounted else base,
                                      is_discounted=discounted))
                if n_opt:
                    o, extra = rng.choice(opts_by_menu[mn])
                    normal = base + extra
                    lines.append(dict(kind="option", menu_id=menus[mn].id, option_id=options[(mn, o)].id,
                                      item_name=mn, option_name=o, qty=n_opt, unit_price_normal=normal,
                                      unit_price=disc(normal, DISCOUNT_RATE) if discounted else normal,
                                      is_discounted=discounted))
            if rng.random() < 0.04:  # 가끔 직접 입력한 추가 식대
                amt = rng.choice([15000, 20000, 30000, 45000])
                lines.append(dict(kind="custom", menu_id=None, option_id=None, item_name="추가 식대",
                                  option_name=None, qty=1, unit_price_normal=amt, unit_price=amt,
                                  is_discounted=False))
            gone = rng.random() < 0.01  # 잘못 넣었다가 지운 주문
            order = m.SalesOrder(place_id=place.id, order_date=d,
                                 source_deleted_at=at(d, 18) if gone else None)
            order.items = [m.SalesOrderItem(line_no=i + 1, price_source="snapshot", **ln) for i, ln in enumerate(lines)]
            s.add(order)
            orders += 1
            deleted += gone
    s.flush()

    # ── 인건비 ──
    logs = 0
    for st in STAFF:
        rng = random.Random(f"{SEED}-staff-{st.name}")
        emp = m.Employee(name=st.name)
        s.add(emp)
        s.flush()
        s.add_all(m.EmployeeWageRate(employee_id=emp.id, hourly_rate=r, valid_from=a, valid_to=b)
                  for r, a, b in st.rates)
        extra_week: dict[tuple[int, int], int] = {}
        for d in days(st.start, until):
            if not is_open(d):
                continue
            wk = d.isocalendar()[:2]
            if wk not in extra_week:  # 이번 주에 하루 더 나오면 무슨 요일인지
                spare = [x for x in range(6) if x not in st.weekdays]
                extra_week[wk] = rng.choice(spare) if rng.random() < st.extra_day else -1
            if d.weekday() not in st.weekdays and d.weekday() != extra_week[wk]:
                continue
            if rng.random() < 0.05:  # 결근
                continue
            begin, end = st.hours
            if rng.random() < 0.2:  # 바쁜 날은 30분 더
                end = time(end.hour, 30)
            s.add(m.WorkLog(employee_id=emp.id, work_date=d, start_time=begin, end_time=end,
                            break_min=30 if rng.random() < 0.3 else 0))
            logs += 1
    s.flush()
    periods = refresh_periods(s)

    # ── 발주 ──
    scats = {}
    for i, name in enumerate(dict.fromkeys(c for c, *_ in SUPPLIES)):
        scats[name] = m.SupplyCategory(name=name, sort_order=i)
    s.add_all(scats.values())
    s.flush()
    items = {}
    for cat, name, unit, price, _ in SUPPLIES:
        items[name] = m.SupplyItem(category_id=scats[cat].id, name=name, unit=unit, ref_price=price)
    reqs = {name: m.Requester(name=name, sort_order=i) for i, name in enumerate(REQUESTERS)}
    cards = [m.PaymentCard(nickname=n, last4=l4) for n, l4 in CARDS]
    s.add_all([*items.values(), *reqs.values(), *cards])
    s.flush()

    rng = random.Random(f"{SEED}-supply")
    supply_orders = paid = 0
    supply_list = list(SUPPLIES)
    for d in days(START, until):
        if not is_open(d) or rng.random() > (0.75 if d in MEMOS else 0.55):
            continue
        who = rng.choice([r for r in REQUESTERS if r != "박도윤" or d >= STAFF[1].start])
        rows = []
        for cat, name, unit, price, (lo, hi) in rng.sample(supply_list, rng.randint(2, 5)):
            qty = rng.randint(lo, hi)
            rows.append(m.SupplyOrderItem(supply_item_id=items[name].id, item_name=name, unit=unit,
                                          unit_price=price, quantity=Decimal(qty), subtotal=price * qty))
        total = sum(r.subtotal for r in rows)
        # 난수는 조건과 상관없이 항상 같은 개수를 뽑는다 (--until이 바뀌어도 다음 날짜의 난수가 밀리지 않게)
        recent_status = rng.choice(["pending", "ordered", "done"])
        status = "done" if (until - d).days > 2 else recent_status
        gone = rng.random() < 0.02
        has_payment = rng.random() < 0.85  # 결제 정보는 선택 입력
        card = rng.choice(cards)
        amount = int(round(total * rng.uniform(0.92, 1.06), -2))
        pay_lag = rng.randint(0, 2)
        order = m.SupplyOrder(requester_id=reqs[who].id, requester_name=who, status=status,
                              memo=MEMOS.get(d) if who == "사장님" else None, estimated_total=total,
                              created_at=at(d, rng.randint(9, 16), rng.choice([0, 15, 30, 45])),
                              source_deleted_at=at(d, 20) if gone else None, items=rows)
        s.add(order)
        s.flush()
        supply_orders += 1
        if status == "done" and not gone and has_payment:
            s.add(m.SupplyPayment(supply_order_id=order.id, paid_amount=amount,
                                  paid_on=min(until, d + timedelta(days=pay_lag)), card_id=card.id,
                                  card_label=f"{card.nickname} {card.last4}"))
            paid += 1

    # ── 기타 ──
    s.add_all(m.DayMemo(memo_date=d, body=b) for d, b in MEMOS.items() if d <= until)
    done_at = at(until, 5)
    stats = {"demo": True, "orders": orders, "work_logs": logs, "supply_orders": supply_orders}
    s.execute(insert(m.SyncRun), [
        {"source": src, "started_at": done_at, "finished_at": done_at + timedelta(seconds=3),
         "status": "succeeded", "stats": stats, "error": None} for src in ("rtdb", "firestore")])
    # 데모 계정: 비밀번호 없음(비밀번호 로그인 불가), 데모 서버의 /api/auth/demo로만 들어간다
    s.add(m.AppUser(email=DEMO_EMAIL, role="owner", password_hash=None))
    s.flush()

    return {"until": until.isoformat(), "sales_orders": orders, "deleted_orders": deleted, "work_logs": logs,
            "payroll_periods": periods, "supply_orders": supply_orders, "supply_payments": paid}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="데모 DB에 가짜 데이터를 다시 만든다")
    ap.add_argument("--until", type=date.fromisoformat, default=datetime.now(KST).date())
    args = ap.parse_args(argv)
    if SessionLocal is None:
        raise SystemExit("DATABASE_URL이 없습니다")
    with SessionLocal.begin() as s:
        db = s.scalar(select(text("current_database()")))
        if not db.endswith("_demo"):
            raise SystemExit(f"'{db}'는 데모 DB가 아닙니다. 이름이 _demo로 끝나는 DB에서만 실행합니다 (모든 데이터를 지움).")
        stats = seed(s, args.until)
    print(stats)


if __name__ == "__main__":
    main()
