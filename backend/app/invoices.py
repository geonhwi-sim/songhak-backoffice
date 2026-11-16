"""정산서 PDF.

- 화면의 "PDF" 버튼: GET /api/settlements/{place_id}/pdf?month=YYYY-MM 이 그때그때 만든다.
- 매달 보관: deploy/daily.sh가 매일 `python -m app.invoices` 를 부른다. 지난달 정산서가 아직 없으면
  정산처마다 PDF를 만들어 INVOICE_DIR(기본 DATA_DIR/invoices)/YYYY-MM/ 에 둔다. 1일에 실패해도 다음 날 다시 만든다.
  이미 있는 파일은 덮어쓰지 않는다. 그달 거래처에 보낸 정산서 원본으로 남기려는 것이다.

거래처에 보내는 문서라서 화면의 내부 표시(추정 가격, 확인 필요)는 넣지 않는다. 할인은 거래처도 알아야 하므로 넣는다.
"""

import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.reports import month_range, settlement_detail, settlements, today_kst

# 글꼴을 줄여 넣을 때 쓰지 않는 표를 버렸다는 안내가 PDF마다 수십 줄 나와서 끈다
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# 서버(Debian)는 fonts-nanum 패키지, 로컬 Windows는 맑은 고딕. FONT_PATH로 바꿀 수 있다.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
]

STORE_NAME = "송학구이"
COLS = [("날짜", 22, "L"), ("항목", 88, "L"), ("수량", 16, "R"), ("단가", 27, "R"), ("금액", 27, "R")]  # mm, 합 180


def _font(candidates: list[str], env: str) -> str:
    for path in [os.environ.get(env), *candidates]:
        if path and Path(path).exists():
            return path
    raise RuntimeError(f"한글 글꼴을 찾지 못했습니다. {env}에 TTF 경로를 지정하세요")


def num(n: int) -> str:
    return f"{n:,}"


def md(d: date) -> str:
    return f"{d.month}/{d.day}"


def render(detail: dict, issued: date | None = None) -> bytes:
    """settlement_detail() 결과 하나를 A4 PDF로 만든다."""
    issued = issued or today_kst()
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font("kr", "", _font(FONT_CANDIDATES, "FONT_PATH"))
    pdf.add_font("kr", "B", _font(BOLD_CANDIDATES, "FONT_BOLD_PATH"))
    year, month = detail["month"].split("-")
    pdf.set_title(f"{detail['name']} {year}년 {int(month)}월 정산서")
    pdf.set_author(STORE_NAME)

    def footer():
        pdf.set_y(-12)
        pdf.set_font("kr", "", 8)
        pdf.set_text_color(130)
        pdf.cell(0, 5, f"{STORE_NAME} · 발행일 {issued:%Y-%m-%d} · {pdf.page_no()}쪽", align="C")
        pdf.set_text_color(0)

    pdf.footer = footer
    pdf.add_page()

    pdf.set_font("kr", "B", 18)
    pdf.cell(0, 10, f"{detail['name']} 정산서", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("kr", "", 10)
    pdf.set_text_color(90)
    n_orders = len(detail["orders"])
    pdf.cell(0, 6, f"{year}년 {int(month)}월 ({md(detail['start'])} ~ {md(detail['end'])}) · 주문 {n_orders}건 · {STORE_NAME}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)
    pdf.ln(3)

    # 거래처가 가장 먼저 보는 것: 청구 금액
    pdf.set_fill_color(248, 244, 240)
    pdf.set_font("kr", "", 10)
    pdf.cell(40, 12, "  청구 금액", fill=True)
    pdf.set_font("kr", "B", 15)
    pdf.cell(0, 12, f"{num(detail['total'])}원  ", align="R", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    def header_row():
        pdf.set_font("kr", "B", 9.5)
        pdf.set_fill_color(240, 235, 230)
        for title, w, align in COLS:
            pdf.cell(w, 7, title, border="B", align=align, fill=True)
        pdf.ln()
        pdf.set_font("kr", "", 9.5)

    header_row()
    for k, o in enumerate(detail["orders"]):
        for i, it in enumerate(o["items"]):
            last_row = k == len(detail["orders"]) - 1 and i == len(o["items"]) - 1
            # 마지막 줄은 합계와 같은 쪽에 오게 한다 (합계만 혼자 다음 쪽으로 넘어가지 않게)
            new_page = pdf.will_page_break(6.5 + (13 if last_row else 0))
            if new_page:
                pdf.add_page()
                header_row()
            name = it["name"] + (f" · {it['option']}" if it["option"] else "") + (" (할인)" if it["is_discounted"] else "")
            # 쪽이 바뀌면 이어지는 주문도 날짜를 다시 쓴다
            show_date = i == 0 or new_page
            cells = [md(o["date"]) if show_date else "", name, num(it["qty"]), num(it["unit_price"]), num(it["amount"])]
            for (_, w, align), text in zip(COLS, cells):
                pdf.cell(w, 6.5, text, border="B" if i == len(o["items"]) - 1 else 0, align=align)
            pdf.ln()

    pdf.ln(2)
    pdf.set_font("kr", "B", 12)
    label_w = sum(w for _, w, _ in COLS[:-2])
    pdf.cell(label_w, 9, "합계")
    pdf.cell(COLS[-2][1] + COLS[-1][1], 9, f"{num(detail['total'])}원", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def file_name(detail: dict) -> str:
    # 파일 이름에 쓸 수 없는 글자만 바꾼다
    safe = "".join("_" if c in '\\/:*?"<>|' else c for c in detail["name"]).strip()
    return f"{detail['month']} {safe} 정산서.pdf"


def last_month(day: date) -> str:
    first = day.replace(day=1)
    prev = date.fromordinal(first.toordinal() - 1)
    return f"{prev.year}-{prev.month:02d}"


def archive_month(session, month: str, out_dir: Path, issued: date | None = None) -> list[Path]:
    """그달 주문이 있는 정산처마다 PDF를 만든다. 이미 있는 파일은 그대로 둔다. 새로 만든 파일 목록을 돌려준다."""
    month_range(month)  # 형식 확인
    folder = out_dir / month
    made = []
    for p in settlements(session, month)["places"]:
        detail = settlement_detail(session, p["place_id"], month)
        if not detail or not detail["orders"]:
            continue
        path = folder / file_name(detail)
        if path.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(render(detail, issued))
        tmp.replace(path)
        made.append(path)
    return made


def main() -> None:
    from sqlalchemy.orm import Session

    from app.db import engine

    if engine is None:
        raise SystemExit("DATABASE_URL이 없습니다")
    month = sys.argv[1] if len(sys.argv) > 1 else last_month(today_kst())
    out_dir = Path(os.environ.get("INVOICE_DIR") or Path(os.environ.get("DATA_DIR", "data")) / "invoices")
    with Session(engine) as s:
        made = archive_month(s, month, out_dir)
    print(f"{datetime.now():%F %T} 정산서 {month}: 새로 {len(made)}개" + "".join(f"\n  {p}" for p in made))


if __name__ == "__main__":
    main()
