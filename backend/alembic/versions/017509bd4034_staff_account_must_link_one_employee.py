"""staff account must link one employee

직원 계정(role = 'staff')은 직원 한 명과 반드시 연결되고, 직원 한 명에 계정은 하나다.
CHECK 제약은 autogenerate가 잡지 못해서 손으로 넣었다.

Revision ID: 017509bd4034
Revises: 4d54351ea085
Create Date: 2026-11-16 19:54:43.569549

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '017509bd4034'
down_revision: Union[str, Sequence[str], None] = '4d54351ea085'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint("staff_has_employee", "app_users", "role = 'owner' OR employee_id IS NOT NULL")
    op.create_unique_constraint(op.f("uq_app_users_employee_id"), "app_users", ["employee_id"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_app_users_employee_id"), "app_users", type_="unique")
    op.drop_constraint(op.f("ck_app_users_staff_has_employee"), "app_users", type_="check")
