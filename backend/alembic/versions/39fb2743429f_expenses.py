"""expenses

가게가 직접 쓴 돈(매장·도매상 장보기, 공과금 등)을 백오피스에서 입력하는 표와, 자연어 질의용 qa.expenses 뷰.
CHECK 제약은 autogenerate가 잡지 못해서 손으로 넣었다.

Revision ID: 39fb2743429f
Revises: 017509bd4034
Create Date: 2026-11-17 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '39fb2743429f'
down_revision: Union[str, Sequence[str], None] = '017509bd4034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'expenses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('spent_on', sa.Date(), nullable=False),
        sa.Column('vendor', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=16), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=16), server_default='manual', nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('amount > 0', name=op.f('ck_expenses_amount_positive')),
        sa.CheckConstraint("category IN ('food', 'supplies', 'utilities', 'rent', 'other')",
                           name=op.f('ck_expenses_category')),
        sa.CheckConstraint("source IN ('manual', 'receipt')", name=op.f('ck_expenses_source')),
        sa.ForeignKeyConstraint(['created_by'], ['app_users.id'], name=op.f('fk_expenses_created_by_app_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_expenses')),
    )
    op.create_index(op.f('ix_expenses_spent_on'), 'expenses', ['spent_on'], unique=False)

    op.execute("""
        CREATE VIEW qa.expenses AS
        SELECT id AS expense_id, spent_on, vendor, category, amount, memo, source
        FROM expenses WHERE deleted_at IS NULL
    """)
    op.execute("COMMENT ON VIEW qa.expenses IS '가게가 직접 쓴 돈(매장·도매상 장보기, 공과금, 임대료). "
               "한 행 = 지출 한 건. 발주 앱(supply_orders)과 따로 입력한다'")
    op.execute("COMMENT ON COLUMN qa.expenses.category IS "
               "'food = 식재료, supplies = 소모품, utilities = 공과금, rent = 임대료, other = 기타'")
    op.execute("COMMENT ON COLUMN qa.expenses.vendor IS '산 곳'")
    op.execute("GRANT SELECT ON qa.expenses TO qa_readonly")


def downgrade() -> None:
    op.execute("DROP VIEW qa.expenses")
    op.drop_index(op.f('ix_expenses_spent_on'), table_name='expenses')
    op.drop_table('expenses')
