"""백오피스 DB 모델. 설계 근거는 docs/erd.md.

원칙
- 원본(Firebase) ID는 source_id로 보존하고 upsert 기준으로 쓴다.
- 지우지 않고 is_active=False로 둔다.
- 금액은 원 단위 정수.
- 가격·할인·시급은 valid_from ~ valid_to(null이면 현재) 이력으로 둔다.
  같은 대상의 기간이 겹치지 않게 하는 EXCLUDE 제약은 마이그레이션에서 SQL로 만든다.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .db import Base

# ── 공통 ──


class SourceMixin:
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str | None] = mapped_column(String(64), unique=True)


class PeriodMixin:
    valid_from: Mapped[date]
    valid_to: Mapped[date | None]  # null = 현재까지

    @declared_attr.directive
    def __table_args__(cls):
        # 테이블마다 새 제약 객체를 만들어야 한다 (하나를 여러 테이블에 붙일 수 없음)
        return (CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="period"),)


# ── 정산 ──


class Place(SourceMixin, Base):
    __tablename__ = "places"
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class MenuCategory(SourceMixin, Base):
    __tablename__ = "menu_categories"
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)


class Menu(SourceMixin, Base):
    __tablename__ = "menus"
    category_id: Mapped[int | None] = mapped_column(ForeignKey("menu_categories.id"))
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    options: Mapped[list["MenuOption"]] = relationship(back_populates="menu")
    prices: Mapped[list["MenuPrice"]] = relationship(back_populates="menu")


class MenuOption(SourceMixin, Base):
    __tablename__ = "menu_options"
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id"))
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    menu: Mapped[Menu] = relationship(back_populates="options")


class MenuPrice(PeriodMixin, Base):
    __tablename__ = "menu_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id"))
    price: Mapped[int] = mapped_column(CheckConstraint("price >= 0", name="price_nonneg"))

    menu: Mapped[Menu] = relationship(back_populates="prices")


class MenuOptionPrice(PeriodMixin, Base):
    __tablename__ = "menu_option_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    option_id: Mapped[int] = mapped_column(ForeignKey("menu_options.id"))
    extra_price: Mapped[int] = mapped_column(CheckConstraint("extra_price >= 0", name="extra_nonneg"))


class PlaceMenuPricing(PeriodMixin, Base):
    """정산처마다 메뉴별로 기본가(normal) 또는 할인가(discount)를 적용한 기간."""

    __tablename__ = "place_menu_pricing"
    id: Mapped[int] = mapped_column(primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    menu_id: Mapped[int] = mapped_column(ForeignKey("menus.id"))
    price_type: Mapped[str] = mapped_column(
        String(10), CheckConstraint("price_type IN ('normal', 'discount')", name="price_type")
    )


class DiscountRate(PeriodMixin, Base):
    __tablename__ = "discount_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), CheckConstraint("rate_percent >= 0 AND rate_percent < 100", name="rate_range")
    )


class SalesOrder(SourceMixin, Base):
    __tablename__ = "sales_orders"
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"), index=True)
    order_date: Mapped[date] = mapped_column(index=True)
    source_deleted_at: Mapped[datetime | None]

    items: Mapped[list["SalesOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderItem.line_no"
    )


class SalesOrderItem(Base):
    """주문 항목. 금액 계산의 기준이 되는 테이블.

    kind
      menu          메뉴 1그릇
      option        옵션 붙은 메뉴 1그릇 (단가 = 기본가 + 추가금)
      legacy_option 예전 방식: 추가금만 (기본 메뉴는 같은 주문의 menu 항목에 따로 있음)
      custom        직접 입력 금액 (추가 식대 등)
    """

    __tablename__ = "sales_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_sales_order_items_line"),
        CheckConstraint("kind IN ('menu', 'option', 'legacy_option', 'custom')", name="kind"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("(kind = 'custom') = (menu_id IS NULL)", name="custom_has_no_menu"),
        CheckConstraint("price_source IN ('snapshot', 'history', 'estimated')", name="price_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16))
    menu_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id"))
    option_id: Mapped[int | None] = mapped_column(ForeignKey("menu_options.id"))
    item_name: Mapped[str] = mapped_column(Text)  # 당시 메뉴 이름 또는 직접 입력 이름
    option_name: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[int]
    unit_price_normal: Mapped[int]  # 할인 전 단가
    unit_price: Mapped[int]  # 실제 청구 단가
    is_discounted: Mapped[bool]
    amount: Mapped[int] = mapped_column(Computed("qty * unit_price"))
    price_source: Mapped[str] = mapped_column(String(10))
    needs_review: Mapped[bool] = mapped_column(default=False, server_default=false())

    order: Mapped[SalesOrder] = relationship(back_populates="items")


# ── 인건비 ──


class Employee(SourceMixin, Base):
    __tablename__ = "employees"
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class EmployeeWageRate(PeriodMixin, Base):
    __tablename__ = "employee_wage_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    hourly_rate: Mapped[int] = mapped_column(CheckConstraint("hourly_rate > 0", name="rate_positive"))


class WorkLog(SourceMixin, Base):
    __tablename__ = "work_logs"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="time_order"),
        CheckConstraint("break_min >= 0", name="break_nonneg"),
    )
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[date] = mapped_column(index=True)
    start_time: Mapped[time]
    end_time: Mapped[time]
    break_min: Mapped[int] = mapped_column(default=0)


# ── 발주 ──


class SupplyCategory(SourceMixin, Base):
    __tablename__ = "supply_categories"
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)


class SupplyItem(SourceMixin, Base):
    __tablename__ = "supply_items"
    category_id: Mapped[int | None] = mapped_column(ForeignKey("supply_categories.id"))
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    ref_price: Mapped[int | None]  # 참고 단가. 실제 비용은 supply_payments
    purchase_url: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class Requester(SourceMixin, Base):
    __tablename__ = "requesters"
    name: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class SupplyOrder(SourceMixin, Base):
    __tablename__ = "supply_orders"
    __table_args__ = (CheckConstraint("status IN ('pending', 'done')", name="status"),)
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("requesters.id"))
    requester_name: Mapped[str] = mapped_column(Text)  # 당시 이름
    status: Mapped[str] = mapped_column(String(10))
    memo: Mapped[str | None] = mapped_column(Text)
    estimated_total: Mapped[int | None]  # 단가표 기준 예상 금액
    created_at: Mapped[datetime] = mapped_column(index=True)
    source_deleted_at: Mapped[datetime | None]

    items: Mapped[list["SupplyOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payment: Mapped["SupplyPayment | None"] = relationship(back_populates="order")


class SupplyOrderItem(Base):
    """원본 발주 항목에는 품목 ID가 없다. 당시 이름·단위·가격을 그대로 저장한다."""

    __tablename__ = "supply_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    supply_order_id: Mapped[int] = mapped_column(ForeignKey("supply_orders.id", ondelete="CASCADE"), index=True)
    supply_item_id: Mapped[int | None] = mapped_column(ForeignKey("supply_items.id"))  # 이름이 같은 품목이 있을 때만
    item_name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[int | None]
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    subtotal: Mapped[int | None]

    order: Mapped[SupplyOrder] = relationship(back_populates="items")


class PaymentCard(Base):
    """세금 계산용 카드 구분. 카드 전체 번호는 저장하지 않는다."""

    __tablename__ = "payment_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(4), CheckConstraint("last4 ~ '^[0-9]{4}$'", name="last4_digits"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class SupplyPayment(Base):
    """관리자가 발주를 완료할 때 입력하는 실제 결제 정보. 모두 선택 입력."""

    __tablename__ = "supply_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    supply_order_id: Mapped[int] = mapped_column(ForeignKey("supply_orders.id"), unique=True)
    paid_amount: Mapped[int | None] = mapped_column(CheckConstraint("paid_amount >= 0", name="paid_nonneg"))
    paid_on: Mapped[date | None]
    card_id: Mapped[int | None] = mapped_column(ForeignKey("payment_cards.id"))
    memo: Mapped[str | None] = mapped_column(Text)

    order: Mapped[SupplyOrder] = relationship(back_populates="payment")


# ── 기타 · 운영 ──


class DayMemo(Base):
    __tablename__ = "day_memos"
    memo_date: Mapped[date] = mapped_column(primary_key=True)
    body: Mapped[str] = mapped_column(Text)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (CheckConstraint("source IN ('rtdb', 'firestore')", name="source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class AppUser(Base):
    __tablename__ = "app_users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'staff')", name="role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(String(10))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
