"""pos sales

OK POS 엑셀에서 올린 홀 매출: 올린 기록(pos_imports), 영업일·포스별 마감(pos_daily_sales),
기간별 메뉴 매출(pos_menu_sales). 자연어 질의 뷰는 아직 두지 않는다 ("매출"의 뜻이 바뀌어 평가를 다시 돌린 뒤 연다).

Revision ID: 398cdfae81d2
Revises: 39fb2743429f
Create Date: 2026-11-17 22:50:29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '398cdfae81d2'
down_revision: Union[str, Sequence[str], None] = '39fb2743429f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pos_imports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('file_name', sa.Text(), nullable=True),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('net_total', sa.Integer(), nullable=False),
    sa.Column('imported_by', sa.Integer(), nullable=True),
    sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('daily', 'menu')", name=op.f('ck_pos_imports_kind')),
    sa.CheckConstraint('period_start <= period_end', name=op.f('ck_pos_imports_period')),
    sa.ForeignKeyConstraint(['imported_by'], ['app_users.id'], name=op.f('fk_pos_imports_imported_by_app_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pos_imports'))
    )
    op.create_table('pos_daily_sales',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('business_date', sa.Date(), nullable=False),
    sa.Column('pos_no', sa.String(length=8), nullable=False),
    sa.Column('gross', sa.Integer(), nullable=False),
    sa.Column('discount', sa.Integer(), nullable=False),
    sa.Column('net', sa.Integer(), nullable=False),
    sa.Column('cash', sa.Integer(), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['import_id'], ['pos_imports.id'], name=op.f('fk_pos_daily_sales_import_id_pos_imports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pos_daily_sales')),
    sa.UniqueConstraint('business_date', 'pos_no', name=op.f('uq_pos_daily_sales_business_date'))
    )
    op.create_table('pos_menu_sales',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('net', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.CheckConstraint('period_start <= period_end', name=op.f('ck_pos_menu_sales_period')),
    sa.ForeignKeyConstraint(['import_id'], ['pos_imports.id'], name=op.f('fk_pos_menu_sales_import_id_pos_imports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pos_menu_sales')),
    sa.UniqueConstraint('period_start', 'period_end', 'code', name=op.f('uq_pos_menu_sales_period_start'))
    )


def downgrade() -> None:
    op.drop_table('pos_menu_sales')
    op.drop_table('pos_daily_sales')
    op.drop_table('pos_imports')
