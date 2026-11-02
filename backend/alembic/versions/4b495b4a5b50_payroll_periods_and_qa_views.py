"""payroll periods and qa views

자연어 질의(A1)용 읽기 전용 구역을 만든다.
- qa 스키마의 뷰만 조회할 수 있는 qa_readonly 역할 (로그인 불가).
  실제 접속 계정은 python -m app.qa.create_user 로 만든다 (비밀번호는 .env에만).
- 뷰는 소유자 권한으로 실행되므로, qa_readonly는 원본 테이블에 아무 권한이 없어도 뷰를 읽을 수 있다.
- 주휴수당처럼 규칙이 복잡한 값은 SQL로 다시 계산하지 않고 payroll_periods(파이썬 계산 결과)를 보여 준다.

Revision ID: 4b495b4a5b50
Revises: c61729bfd335
Create Date: 2026-11-02 21:01:14.092394

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b495b4a5b50'
down_revision: Union[str, Sequence[str], None] = 'c61729bfd335'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VIEWS = {
    "places": (
        "정산처 목록",
        """
        SELECT id AS place_id, name, is_active FROM places
        """,
    ),
    "sales_items": (
        "정산 주문 항목. 한 행 = 주문 한 건의 한 줄. 원본에서 지워진 주문은 뺐다. 정산 금액 = sum(amount)",
        """
        SELECT i.id AS item_id, o.id AS order_id, o.order_date, p.id AS place_id, p.name AS place_name,
               i.kind, i.item_name, i.option_name, i.qty, i.unit_price_normal, i.unit_price, i.amount,
               i.qty * (i.unit_price_normal - i.unit_price) AS discount_amount,
               i.is_discounted, i.price_source, i.needs_review
        FROM sales_order_items i
        JOIN sales_orders o ON o.id = i.order_id
        JOIN places p ON p.id = o.place_id
        WHERE o.source_deleted_at IS NULL
        """,
    ),
    "work_logs": (
        "직원 근무 기록. 한 행 = 하루 근무",
        """
        SELECT e.id AS employee_id, e.name AS employee_name, w.work_date, w.start_time, w.end_time, w.break_min,
               round((extract(epoch FROM (w.end_time - w.start_time)) / 3600 - w.break_min / 60.0)::numeric, 2)
                   AS hours
        FROM work_logs w JOIN employees e ON e.id = w.employee_id
        """,
    ),
    "payroll": (
        "급여 기간별 직원 인건비. month '2026-11' = 2026-10-17 ~ 2026-11-16. total = base_pay + juhu_pay",
        """
        SELECT pp.month, pp.period_start, pp.period_end, e.id AS employee_id, e.name AS employee_name,
               pp.days, pp.hours, pp.base_pay, pp.juhu_weeks, pp.juhu_pay, pp.total
        FROM payroll_periods pp JOIN employees e ON e.id = pp.employee_id
        """,
    ),
    "supply_orders": (
        "발주. order_date는 한국 날짜. cost = 결제 금액, 없으면 예상 금액. "
        "removed_from_app은 발주 앱에서 7일이 지나 지워졌다는 뜻일 뿐 실제 발주는 맞다",
        """
        SELECT o.id AS order_id, (o.created_at AT TIME ZONE 'Asia/Seoul')::date AS order_date,
               o.created_at AT TIME ZONE 'Asia/Seoul' AS created_at_kst,
               o.requester_name, o.status, o.memo, o.estimated_total,
               p.paid_amount, p.paid_on, p.card_label,
               COALESCE(p.paid_amount, o.estimated_total, 0) AS cost,
               o.source_deleted_at IS NOT NULL AS removed_from_app
        FROM supply_orders o LEFT JOIN supply_payments p ON p.supply_order_id = o.id
        """,
    ),
    "supply_order_items": (
        "발주 품목. 한 행 = 발주 한 건의 품목 하나",
        """
        SELECT i.id AS item_id, i.supply_order_id AS order_id,
               (o.created_at AT TIME ZONE 'Asia/Seoul')::date AS order_date,
               i.item_name, c.name AS category, i.unit, i.unit_price, i.quantity, i.subtotal
        FROM supply_order_items i
        JOIN supply_orders o ON o.id = i.supply_order_id
        LEFT JOIN supply_items si ON si.id = i.supply_item_id
        LEFT JOIN supply_categories c ON c.id = si.category_id
        """,
    ),
}

COLUMN_COMMENTS = {
    ("sales_items", "kind"): "menu = 메뉴, custom = 직접 입력 금액",
    ("sales_items", "unit_price_normal"): "할인 전 단가",
    ("sales_items", "unit_price"): "실제 청구 단가",
    ("sales_items", "price_source"): "snapshot = 주문 당시 저장값, history = 가격 이력, estimated = 추정",
    ("sales_items", "needs_review"): "지운 옵션·메뉴를 복원해 계산한 항목 (확인 필요)",
    ("payroll", "juhu_pay"): "주휴수당",
    ("payroll", "juhu_weeks"): "주휴수당을 받은 주 수",
    ("supply_orders", "status"): "pending = 대기, ordered = 주문함, done = 완료",
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('payroll_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('employee_id', sa.Integer(), nullable=False),
    sa.Column('month', sa.String(length=7), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('days', sa.Integer(), nullable=False),
    sa.Column('hours', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('base_pay', sa.Integer(), nullable=False),
    sa.Column('juhu_weeks', sa.Integer(), nullable=False),
    sa.Column('juhu_pay', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_payroll_periods_employee_id_employees')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_periods')),
    sa.UniqueConstraint('employee_id', 'month', name=op.f('uq_payroll_periods_employee_id'))
    )

    def quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    op.execute("CREATE SCHEMA qa")
    for name, (comment, sql) in VIEWS.items():
        op.execute(f"CREATE VIEW qa.{name} AS {sql}")
        op.execute(f"COMMENT ON VIEW qa.{name} IS {quote(comment)}")
    for (view, col), comment in COLUMN_COMMENTS.items():
        op.execute(f"COMMENT ON COLUMN qa.{view}.{col} IS {quote(comment)}")

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'qa_readonly') THEN
                CREATE ROLE qa_readonly NOLOGIN;
            END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA qa TO qa_readonly")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA qa TO qa_readonly")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP SCHEMA qa CASCADE")
    op.execute("DROP ROLE IF EXISTS qa_readonly")
    op.drop_table('payroll_periods')
