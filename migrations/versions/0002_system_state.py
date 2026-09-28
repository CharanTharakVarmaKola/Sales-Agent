"""Alembic migration 0002_system_state — kill-switch flag store.

Additive only: one new table, zero changes to existing tables.
The stop flag persists here; the orchestrator checks it before dispatch
and the sender before every send. Initial state is Stopped (safe default).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_system_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

STOPPED_DEFAULT = (
    '{"stopped": true,'
    ' "reason": "Default safe state. Outreach not yet enabled.",'
    ' "by": "system", "at": "", "automatic": false}')


def upgrade() -> None:
    op.create_table(
        "system_state",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
    )
    op.execute(
        "INSERT INTO system_state (key, value, updated_at, updated_by, reason)"
        f" VALUES ('stop', '{STOPPED_DEFAULT}', '', 'system',"
        " 'Default safe state. Outreach not yet enabled.')")


def downgrade() -> None:
    op.drop_table("system_state")
