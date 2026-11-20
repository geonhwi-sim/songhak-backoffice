"""OK POS에서 내려받은 엑셀(.xlsx) 읽기. 화면에서 올린 파일을 미리 보고, 확인하면 DB에 넣는다.

받는 파일 두 가지 (OK POS 웹 관리자에서 내려받은 그대로)
- 'POS정산내역': 영업일·포스별 하루 마감. 총매출·할인·실매출·현금매출 → pos_daily_sales
- '상품 ABC 분석': 기간 동안 메뉴별 실매출·판매수량 → pos_menu_sales

원칙
- 엑셀 라이브러리 없이 표준 라이브러리(zipfile + xml)로 읽는다. 필요한 건 첫 시트의 값뿐이다.
- 열은 위치가 아니라 머리글 이름으로 찾는다 (POS 화면 설정에 따라 열 순서가 바뀔 수 있다).
- 파일의 '합계' 줄과 각 줄을 더한 값이 다르면 받지 않는다 (잘리거나 손댄 파일).
- 같은 기간을 다시 올리면 그 기간 자료를 통째로 바꾼다 (POS에서 마감을 고친 뒤 다시 받는 경우).
- 외상 거래처 주문은 POS에 '주문확인용' 품목으로 찍는다. 이 금액은 정산 앱의 외상과 같은 돈이라
  손익에서 POS 매출과 외상 정산을 더할 때 한 번 뺀다 (app/reports.py).
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import models as m

KST = timezone(timedelta(hours=9))
MAX_UPLOAD = 1_000_000  # 한 달 파일이 15KB 정도. nginx도 1MB까지 받는다
MAX_UNZIPPED = 20_000_000  # 압축을 푼 크기 상한 (압축 폭탄 막기)
MAX_ROWS = 5000
MAX_PERIOD_DAYS = 366
CREDIT_ITEMS = ("주문확인용",)  # 외상 거래처 주문을 찍는 품목 (정산 앱의 외상과 같은 돈)

DAILY_TITLE, MENU_TITLE = "POS정산내역", "상품ABC분석"
DAILY_COLUMNS = {"영업일자": "date", "포스번호": "pos_no", "마감구분": "close_type", "등록일시": "closed_at",
                 "총매출액": "gross", "총할인액": "discount", "실매출액": "net", "현금매출액": "cash"}
MENU_COLUMNS = {"대분류": "category", "상품코드": "code", "상품명": "name", "실매출액": "net", "판매수량": "qty"}

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class PosFileError(ValueError):
    """사장님에게 그대로 보여 줄 한 문장."""


# ── xlsx → 행 목록 ──


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _read_member(z: zipfile.ZipFile, name: str) -> bytes:
    info = z.getinfo(name)
    if info.file_size > MAX_UNZIPPED:
        raise PosFileError("엑셀 파일이 너무 큽니다.")
    with z.open(info) as f:
        data = f.read(MAX_UNZIPPED + 1)
    if len(data) > MAX_UNZIPPED:
        raise PosFileError("엑셀 파일이 너무 큽니다.")
    return data


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    """workbook.xml의 첫 시트가 가리키는 파일. 못 찾으면 흔한 이름으로."""
    try:
        wb = ET.fromstring(_read_member(z, "xl/workbook.xml"))
        rid = wb.find(f"{NS}sheets/{NS}sheet").get(f"{REL_NS}id")
        rels = ET.fromstring(_read_member(z, "xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.iter(f"{PKG_REL_NS}Relationship") if r.get("Id") == rid)
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if path in z.namelist():
            return path
    except (KeyError, AttributeError, StopIteration, ET.ParseError):
        pass
    return "xl/worksheets/sheet1.xml"


def read_rows(data: bytes) -> list[list[str | None]]:
    """첫 시트의 값을 행·열 목록으로. 빈 칸은 None, 값은 모두 문자열(숫자도 엑셀에 적힌 그대로)."""
    if len(data) > MAX_UPLOAD:
        raise PosFileError(f"파일은 {MAX_UPLOAD // 1_000_000}MB까지 올릴 수 있습니다.")
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise PosFileError("옛 엑셀(.xls) 형식입니다. 엑셀에서 열어 .xlsx로 저장한 뒤 올려 주세요.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PosFileError("엑셀(.xlsx) 파일이 아닙니다.") from None
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED * 2:
            raise PosFileError("엑셀 파일이 너무 큽니다.")
        try:
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                for si in ET.fromstring(_read_member(z, "xl/sharedStrings.xml")).iter(f"{NS}si"):
                    strings.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
            sheet = ET.fromstring(_read_member(z, _first_sheet_path(z)))
        except (KeyError, ET.ParseError):
            raise PosFileError("엑셀 파일을 읽지 못했습니다. POS에서 다시 내려받아 주세요.") from None

    rows: list[list[str | None]] = []
    for r in sheet.iter(f"{NS}row"):
        if len(rows) >= MAX_ROWS:
            raise PosFileError(f"줄이 너무 많습니다 ({MAX_ROWS}줄까지).")
        idx = int(r.get("r", len(rows) + 1)) - 1
        while len(rows) < idx:
            rows.append([])
        row: list[str | None] = []
        for c in r.iter(f"{NS}c"):
            col = _col_index(c.get("r", "")) if c.get("r") else len(row)
            t, v = c.get("t"), c.find(f"{NS}v")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{NS}t"))
            elif v is None or v.text is None:
                val = None
            elif t == "s":
                try:
                    val = strings[int(v.text)]
                except (ValueError, IndexError):
                    raise PosFileError("엑셀 파일을 읽지 못했습니다. POS에서 다시 내려받아 주세요.") from None
            else:
                val = v.text
            while len(row) < col:
                row.append(None)
            row.append(val if val != "" else None)
        rows.append(row)
    return rows


# ── 행 목록 → POS 자료 ──


@dataclass
class DailyRow:
    business_date: date
    pos_no: str
    gross: int
    discount: int
    net: int
    cash: int
    closed_at: datetime | None


@dataclass
class MenuRow:
    category: str | None
    code: str
    name: str
    net: int
    qty: int


@dataclass
class Parsed:
    kind: str  # 'daily' | 'menu'
    start: date
    end: date
    daily: list[DailyRow] = field(default_factory=list)
    menu: list[MenuRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def net_total(self) -> int:
        return sum(x.net for x in (self.daily if self.kind == "daily" else self.menu))


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def _cell(row: list, i: int) -> str | None:
    return row[i] if i < len(row) else None


def _int(value: str | None, what: str, line: int) -> int:
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise PosFileError(f"{line}번째 줄의 {what} 값을 숫자로 읽지 못했습니다: {value}") from None
    if d != d.to_integral_value():
        raise PosFileError(f"{line}번째 줄의 {what} 값이 원 단위 정수가 아닙니다: {value}")
    return int(d)


def _date(value: str | None, line: int) -> date:
    v = (value or "").strip()
    try:
        if re.fullmatch(r"\d+(\.0+)?", v):  # 엑셀 날짜 일련번호 (1900 체계)
            return date(1899, 12, 30) + timedelta(days=int(float(v)))
        return date.fromisoformat(v[:10].replace(".", "-").replace("/", "-"))
    except (ValueError, OverflowError):
        raise PosFileError(f"{line}번째 줄의 영업일자를 읽지 못했습니다: {value}") from None


def _period(rows: list[list]) -> tuple[date, date]:
    for row in rows[:6]:
        for v in row:
            mt = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", v or "")
            if mt:
                try:
                    start, end = date.fromisoformat(mt[1]), date.fromisoformat(mt[2])
                except ValueError:
                    break
                if start > end:
                    raise PosFileError("조회 기간의 시작이 끝보다 늦습니다.")
                if (end - start).days >= MAX_PERIOD_DAYS:
                    raise PosFileError("조회 기간은 1년 이내로 받아 주세요.")
                return start, end
    raise PosFileError("파일 위쪽에서 조회 기간(YYYY-MM-DD ~ YYYY-MM-DD)을 찾지 못했습니다.")


def _header(rows: list[list], first: str, wanted: dict[str, str], two_rows: bool) -> tuple[int, dict[str, int]]:
    """머리글 줄을 찾아 {내부 이름: 열 번호}. 정산내역은 머리글이 두 줄이다 ('매출' 아래 총매출액·할인·실매출)."""
    for i, row in enumerate(rows[:10]):
        if _norm(_cell(row, 0)) != first:
            continue
        below = rows[i + 1] if two_rows and i + 1 < len(rows) else []
        names: dict[str, int] = {}
        for col in range(max(len(row), len(below))):
            for v in (_cell(below, col), _cell(row, col)):
                if _norm(v) in wanted and wanted[_norm(v)] not in names:
                    names[wanted[_norm(v)]] = col
        missing = [k for k, v in wanted.items() if v not in names]
        if missing:
            raise PosFileError(f"필요한 열이 없습니다: {', '.join(missing)}")
        return i + (2 if two_rows else 1), names
    raise PosFileError("머리글 줄을 찾지 못했습니다. POS에서 내려받은 파일을 그대로 올려 주세요.")


def _total_row(rows: list[list], start: int) -> int:
    for i in range(start, len(rows)):
        if _norm(_cell(rows[i], 0)) == "합계":
            return i
    raise PosFileError("'합계' 줄이 없습니다. 파일이 잘렸는지 확인해 주세요.")


def _parse_daily(rows: list[list], start: date, end: date) -> Parsed:
    first, col = _header(rows, "No.", DAILY_COLUMNS, two_rows=True)
    last = _total_row(rows, first)
    out = Parsed("daily", start, end)
    seen: set[tuple[date, str]] = set()
    mismatch = 0
    for i in range(first, last):
        row, line = rows[i], i + 1
        get = lambda k: _cell(row, col[k])  # noqa: E731
        if not any(row):
            continue
        if get("gross") is None:  # 그 포스에서 마감한 기록이 없는 날 (쉬는 날, 안 쓰는 포스)
            continue
        close_type = _norm(get("close_type"))
        if close_type != "일마감":
            raise PosFileError(f"{line}번째 줄이 '{close_type}'입니다. 일마감만 있는 정산내역으로 받아 주세요.")
        d = _date(get("date"), line)
        if not start <= d <= end:
            raise PosFileError(f"{line}번째 줄의 영업일자({d})가 조회 기간 밖입니다.")
        pos_no = (get("pos_no") or "").strip() or "01"
        if (d, pos_no) in seen:
            raise PosFileError(f"{d} 포스 {pos_no}의 마감이 두 번 있습니다.")
        seen.add((d, pos_no))
        x = DailyRow(d, pos_no, _int(get("gross"), "총매출액", line), _int(get("discount") or 0, "총할인액", line),
                     _int(get("net"), "실매출액", line), _int(get("cash") or 0, "현금매출액", line), None)
        try:
            x.closed_at = datetime.fromisoformat(get("closed_at")).replace(tzinfo=KST) if get("closed_at") else None
        except ValueError:
            x.closed_at = None
        mismatch += x.gross - x.discount != x.net
        out.daily.append(x)

    total = rows[last]
    for key, what in (("gross", "총매출액"), ("net", "실매출액")):
        expected = _int(_cell(total, col[key]) or 0, f"합계 {what}", last + 1)
        got = sum(getattr(x, key) for x in out.daily)
        if got != expected:
            raise PosFileError(f"줄마다 더한 {what}({got:,}원)이 파일의 합계({expected:,}원)와 다릅니다.")
    if not out.daily:
        raise PosFileError("조회 기간에 마감한 날이 없습니다.")
    if mismatch:
        out.warnings.append(f"{mismatch}일은 총매출 − 할인이 실매출과 다릅니다. 실매출액을 매출로 씁니다.")
    return out


def _parse_menu(rows: list[list], start: date, end: date) -> Parsed:
    first, col = _header(rows, "등급", MENU_COLUMNS, two_rows=False)
    last = _total_row(rows, first)
    out = Parsed("menu", start, end)
    codes: set[str] = set()
    for i in range(first, last):
        row, line = rows[i], i + 1
        get = lambda k: _cell(row, col[k])  # noqa: E731
        if not any(row):
            continue
        code, name = (get("code") or "").strip(), (get("name") or "").strip()
        if not code or not name:
            raise PosFileError(f"{line}번째 줄에 상품코드나 상품명이 없습니다.")
        if code in codes:
            raise PosFileError(f"상품코드 {code}가 두 번 있습니다.")
        codes.add(code)
        out.menu.append(MenuRow((get("category") or "").strip() or None, code[:32], name[:200],
                                _int(get("net") or 0, "실매출액", line), _int(get("qty") or 0, "판매수량", line)))

    total = rows[last]
    for key, what, unit in (("net", "실매출액", "원"), ("qty", "판매수량", "개")):
        expected = _int(_cell(total, col[key]) or 0, f"합계 {what}", last + 1)
        got = sum(getattr(x, key) for x in out.menu)
        if got != expected:
            raise PosFileError(f"줄마다 더한 {what}({got:,}{unit})이 파일의 합계({expected:,}{unit})와 다릅니다.")
    if not out.menu:
        raise PosFileError("메뉴 줄이 없습니다.")
    if start.replace(day=1) != end.replace(day=1):
        out.warnings.append("두 달에 걸친 기간입니다. 손익에서 외상(주문확인용)을 빼려면 한 달 안의 기간으로 받아 주세요.")
    if not any(x.name in CREDIT_ITEMS for x in out.menu):
        out.warnings.append(f"'{CREDIT_ITEMS[0]}' 품목이 없습니다. 이 기간에 POS로 찍은 외상이 없다면 괜찮습니다.")
    return out


def parse(data: bytes) -> Parsed:
    rows = read_rows(data)
    title = _norm(next((v for row in rows[:3] for v in row if v), ""))
    if title == DAILY_TITLE:
        parser = _parse_daily
    elif title == MENU_TITLE:
        parser = _parse_menu
    else:
        raise PosFileError("OK POS의 'POS정산내역' 또는 '상품 ABC 분석' 엑셀이 아닙니다.")
    start, end = _period(rows)
    return parser(rows, start, end)


# ── 미리보기 · 넣기 ──


def _existing(session: Session, p: Parsed) -> dict:
    """이번 파일로 바뀔 기존 자료."""
    if p.kind == "daily":
        t = m.PosDailySale
        n, net = session.execute(select(func.count(), func.coalesce(func.sum(t.net), 0))
                                 .where(t.business_date.between(p.start, p.end))).one()
        return {"rows": n, "net_total": int(net)}
    t = m.PosMenuSale
    q = select(t.period_start, t.period_end, func.count(), func.coalesce(func.sum(t.net), 0)).where(
        t.period_start <= p.end, t.period_end >= p.start).group_by(t.period_start, t.period_end)
    periods = [{"start": a, "end": b, "rows": n, "net_total": int(s)} for a, b, n, s in session.execute(q)]
    return {"rows": sum(x["rows"] for x in periods), "net_total": sum(x["net_total"] for x in periods),
            "periods": periods}


def summary(session: Session, p: Parsed) -> dict:
    out = {"kind": p.kind, "start": p.start, "end": p.end, "net_total": p.net_total, "warnings": p.warnings,
           "replaces": _existing(session, p)}
    if p.kind == "daily":
        by_day: dict[date, int] = {}
        for x in p.daily:
            by_day[x.business_date] = by_day.get(x.business_date, 0) + x.net
        out |= {"rows": len(p.daily), "days": len(by_day), "first": min(by_day), "last": max(by_day),
                "gross_total": sum(x.gross for x in p.daily), "discount_total": sum(x.discount for x in p.daily),
                "cash_total": sum(x.cash for x in p.daily)}
    else:
        top = sorted(p.menu, key=lambda x: -x.net)[:5]
        out |= {"rows": len(p.menu), "qty_total": sum(x.qty for x in p.menu),
                "credit_total": sum(x.net for x in p.menu if x.name in CREDIT_ITEMS),
                "top": [{"name": x.name, "net": x.net, "qty": x.qty} for x in top]}
    return out


def apply(session: Session, p: Parsed, user_id: int | None, file_name: str | None) -> dict:
    """같은 기간의 기존 자료를 지우고 새로 넣는다. 커밋은 부르는 쪽에서."""
    result = summary(session, p)
    imp = m.PosImport(kind=p.kind, period_start=p.start, period_end=p.end, file_name=file_name,
                      rows=result["rows"], net_total=p.net_total, imported_by=user_id)
    session.add(imp)
    session.flush()
    if p.kind == "daily":
        t = m.PosDailySale
        session.execute(delete(t).where(t.business_date.between(p.start, p.end)))
        session.add_all(t(business_date=x.business_date, pos_no=x.pos_no, gross=x.gross, discount=x.discount,
                          net=x.net, cash=x.cash, closed_at=x.closed_at, import_id=imp.id) for x in p.daily)
    else:
        t = m.PosMenuSale
        # 기간이 겹치는 메뉴 자료는 지운다 (겹친 채로 두면 같은 판매를 두 번 센다)
        session.execute(delete(t).where(t.period_start <= p.end, t.period_end >= p.start))
        session.add_all(t(period_start=p.start, period_end=p.end, category=x.category, code=x.code, name=x.name,
                          net=x.net, qty=x.qty, import_id=imp.id) for x in p.menu)
    session.flush()
    return {**result, "import_id": imp.id}
