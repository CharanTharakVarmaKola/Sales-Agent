"""Alembic migration 0001_initial — Phase B Batch B1.

Implements Decision 8 schema (10 tables) with the 4 review fixes:
1. leads email uniqueness is UNIQUE(customer_id, email), not plain UNIQUE.
2. messages.quarantine_reason column + enforcement note (read paths must
   check it before LLM access; enforced at runtime by Store.fetch_for_llm).
3. audit hash-chain columns (prev_hash/row_hash); inserts occur inside the
   same transaction as the mutation (see orchestrator/store.py transact).
4. Partial + composite indexes for sweep/requeue paths.

Reversible: downgrade drops indexes then tables in dependency order.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

INTENT_6WAY = ("interested", "not_interested", "objection",
               "auto_reply", "bounce", "unsubscribe")


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text),
        sa.Column("domain", sa.Text, unique=True),
        sa.Column("industry", sa.Text, nullable=True),
        sa.Column("size_band", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.Integer,
                  sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("full_name", sa.Text),
        sa.Column("stage", sa.Text, nullable=False, server_default="new"),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("source", sa.Text),
        sa.Column("next_action_at", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        # FIX 1: composite uniqueness — same address may legitimately
        # appear under two companies; plain UNIQUE(email) would reject it.
        sa.UniqueConstraint("customer_id", "email", name="uq_leads_customer_email"),
    )
    op.create_table(
        "threads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.Integer,
                  sa.ForeignKey("leads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel", sa.Text, nullable=False, server_default="email"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("subject", sa.Text, nullable=True),
        sa.Column("run_id", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer,
                  sa.ForeignKey("threads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("body_raw", sa.Text, nullable=False),
        sa.Column("body_sanitized", sa.Text, nullable=True),
        # FIX 2: non-null quarantine_reason => row must never reach an LLM
        # or sender before human approval (Store.fetch_for_llm enforces).
        sa.Column("quarantine_reason", sa.Text, nullable=True),
        sa.Column("provider_message_id", sa.Text, nullable=True),
        sa.Column("sent_at", sa.Text, nullable=True),
        sa.Column("received_at", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.Text),
        sa.Column("content_hash", sa.Text),
        sa.Column("storage_path", sa.Text),
        sa.Column("trusted", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "intents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer,
                  sa.ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("intent", sa.Text, nullable=False),
        sa.Column("sub_label", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float),
        sa.Column("capability_ref", sa.Text, nullable=True),
        sa.Column("model_route", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("lead_id", sa.Integer,
                  sa.ForeignKey("leads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column("draft_hash", sa.Text),
        sa.Column("batch_id", sa.Text),
        sa.Column("worker_id", sa.Text, nullable=True),
        sa.Column("lease_expires", sa.Text, nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.Text, nullable=True),
        sa.Column("executed_at", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_key", sa.Text, nullable=False, unique=True),
        sa.Column("role_desc", sa.Text),
        sa.Column("persona_json", sa.Text),
        sa.Column("model_route", sa.Text, nullable=True),
        sa.Column("active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_table(
        "audit",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text),
        sa.Column("entity_id", sa.Text),
        sa.Column("payload_json", sa.Text),
        # FIX 3: hash-chain for tamper evidence; INSERT inside the same
        # transaction as the mutation (single-writer lock assumed).
        sa.Column("prev_hash", sa.Text),
        sa.Column("row_hash", sa.Text, nullable=False),
    )
    op.create_table(
        "pending_export",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("enqueued_ts", sa.Text, nullable=False),
        sa.Column("claimed_ts", sa.Text, nullable=True),
        sa.Column("done_ts", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    )
    # FIX 4: sweep/requeue indexes (partial where SQLite supports it).
    op.create_index("idx_leads_next_action", "leads", ["next_action_at"],
                    sqlite_where=sa.text("next_action_at IS NOT NULL"))
    op.create_index("idx_actions_status_lease", "actions", ["status", "lease_expires"])
    op.create_index("idx_messages_provider_msg", "messages", ["provider_message_id"],
                    unique=True, sqlite_where=sa.text("provider_message_id IS NOT NULL"))
    op.create_index("idx_threads_lead", "threads", ["lead_id"])
    op.create_index("idx_messages_thread", "messages", ["thread_id"])
    op.create_index("idx_audit_entity", "audit", ["entity_type", "entity_id"])
    op.create_index("idx_pending_export_done", "pending_export", ["done_ts"])


def downgrade() -> None:
    for name in ("idx_pending_export_done", "idx_audit_entity",
                 "idx_messages_thread", "idx_threads_lead",
                 "idx_messages_provider_msg", "idx_actions_status_lease",
                 "idx_leads_next_action"):
        try:
            op.drop_index(name)
        except Exception:
            pass
    for table in ("pending_export", "audit", "agents", "actions", "intents",
                  "attachments", "messages", "threads", "leads", "customers"):
        op.drop_table(table)
