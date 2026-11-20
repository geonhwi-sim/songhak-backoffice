"""테스트용 OK POS 엑셀 만들기. 실제 POS 파일과 같은 모양(제목·기간 줄, 두 줄 머리글, 공유 문자열, 합계 줄)에
가짜 숫자만 넣는다. E2E가 올릴 파일도 이걸로 만든다:  python -m tests.pos_files <저장할 폴더>
"""

import io
import sys
import zipfile
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""
ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""
WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""


def col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx(rows: list[list]) -> bytes:
    """rows: 줄마다 칸 값 목록. str은 공유 문자열, int·float는 숫자, None은 빈 칸. 빈 줄([])은 건너뛴다."""
    strings: list[str] = []
    out = []
    for r, row in enumerate(rows, start=1):
        if not row:
            continue
        cells = []
        for c, v in enumerate(row):
            ref = f"{col(c)}{r}"
            if v is None:
                cells.append(f'<c r="{ref}"/>')
            elif isinstance(v, str):
                strings.append(v)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = f'<?xml version="1.0" encoding="UTF-8"?><worksheet {ns}><sheetData>{"".join(out)}</sheetData></worksheet>'
    sst = (f'<?xml version="1.0" encoding="UTF-8"?><sst {ns} count="{len(strings)}">'
           + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def daily_rows(start: date, end: date, days: list[tuple], total: dict | None = None) -> list[list]:
    """days: (날짜, 총매출, 할인, 현금) 목록. 포스 02는 늘 빈 줄로 넣는다 (실제 파일처럼)."""
    rows: list[list] = [
        ["POS정산내역"], [],
        [f"조회일자: {start} ~ {end}, 검색대상 : 테스트 매장"],
        ["No.", "영업일자", "포스\n번호", "마감구분", "등록일시", "매출", None, None, "영업\n준비금", "현금\n매출액",
         "입출금", None, "현금\n시재액", "현금\n과부족", "보증금\n건수", "보증금\n총액"],
        [None, None, None, None, None, "총매출액", "총할인액", "실매출액", None, None, "입금액", "출금액"],
    ]
    n = 0
    sums = {"gross": 0, "discount": 0, "net": 0, "cash": 0}
    for d, gross, discount, cash in days:
        net = gross - discount
        n += 1
        rows.append([str(n), serial(d), "01", "일마감", f"{d} 22:10:00", gross, discount, net, 0, cash, 0, 0, 0,
                     -cash, 0, 0])
        n += 1
        rows.append([str(n), serial(d), "02", "일마감", None, None, None, None, None, None, None, None, None,
                     None, 0, 0])
        for k, v in (("gross", gross), ("discount", discount), ("net", net), ("cash", cash)):
            sums[k] += v
    sums |= total or {}
    rows.append(["합계", None, None, None, None, sums["gross"], sums["discount"], sums["net"], 0, sums["cash"], 0, 0,
                 0, -sums["cash"], 0, 0])
    return rows


def menu_rows(start: date, end: date, items: list[tuple], total: dict | None = None) -> list[list]:
    """items: (대분류, 상품코드, 상품명, 실매출, 수량) 목록."""
    rows: list[list] = [
        ["상품 ABC 분석"], [],
        [f"기간 : {start} ~ {end}, 매장 : 테스트 매장, A등급:  70, B등급:  90, C등급:  100, 금액ABC"],
        ["등급", "No.", "대분류", "상품코드", "상품명", "실매출액", "판매수량", "점유율 (%)", "누계 (%)"],
    ]
    for i, (cat, code, name, net, qty) in enumerate(items, start=1):
        rows.append(["A", str(i), cat, code, name, net, qty, 0, 0])
    sums = {"net": sum(x[3] for x in items), "qty": sum(x[4] for x in items)} | (total or {})
    rows.append(["합계", None, None, None, None, sums["net"], sums["qty"], None, None])
    return rows


# E2E가 공개 데모에 올리는 파일: 2020년 1월 (데모 데이터는 2026-07부터라 겹치지 않는다)
E2E_START, E2E_END = date(2020, 1, 1), date(2020, 1, 31)
E2E_DAYS = [(date(2020, 1, 2), 812000, 12000, 95000), (date(2020, 1, 3), 1204500, 0, 130000),
            (date(2020, 1, 4), 990000, 10000, 88000)]
E2E_MENU = [("식사 메뉴", "000001", "물냉면", 1840000, 230), ("주류", "800001", "소주", 620000, 124),
            ("외상", "900001", "주문확인용", 524500, 4)]


def write_e2e_files(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "POS정산내역_202001.xlsx").write_bytes(xlsx(daily_rows(E2E_START, E2E_END, E2E_DAYS)))
    (folder / "상품ABC분석_202001.xlsx").write_bytes(xlsx(menu_rows(E2E_START, E2E_END, E2E_MENU)))


if __name__ == "__main__":
    write_e2e_files(Path(sys.argv[1]))
