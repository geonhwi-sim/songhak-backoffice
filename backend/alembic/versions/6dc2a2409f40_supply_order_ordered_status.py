"""supply order ordered status

Revision ID: 6dc2a2409f40
Revises: fb2758cafd40
Create Date: 2026-10-19 21:48:05.483736

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6dc2a2409f40'
down_revision: Union[str, Sequence[str], None] = 'fb2758cafd40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 발주 앱에 "주문함" 단계가 생겼다: 대기(pending) → 주문함(ordered) → 입고(done)
# autogenerate는 CHECK 제약 변경을 감지하지 못해 직접 쓴다.


def upgrade() -> None:
    op.drop_constraint(op.f("ck_supply_orders_status"), "supply_orders", type_="check")
    op.create_check_constraint("status", "supply_orders", "status IN ('pending', 'ordered', 'done')")


def downgrade() -> None:
    # 되돌리면 주문함 상태는 대기로 바꾼다 (입고되지 않은 발주라서)
    op.execute("UPDATE supply_orders SET status = 'pending' WHERE status = 'ordered'")
    op.drop_constraint(op.f("ck_supply_orders_status"), "supply_orders", type_="check")
    op.create_check_constraint("status", "supply_orders", "status IN ('pending', 'done')")
