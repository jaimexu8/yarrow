"""service_nodes, the last known health of every worker node and inference
server, so the admin dashboard can show each one (US-76)

Revision ID: 0025
Revises: 0023
Create Date: 2026-11-04 12:00:00.000000

A live Celery ping only lists workers that answer. Keeping a row per node
means a worker that stops answering is still listed, as down, after it
disappears from the broker.

- id: primary key.
- kind: "worker" for a Celery worker, "inference" for an inference server.
- name: the Celery hostname ("celery@host") for a worker, the configured
  target name for an inference server. Unique per kind.
- hostname: the host part only, for display.
- url: the health URL that is probed. Inference servers only.
- status: healthy, degraded, down or unknown (never checked yet).
- latency_ms: response time of the last successful check.
- last_checked_at: when the node was last checked.
- last_seen_at: when the node last answered. Kept when it stops answering,
  so the dashboard can say how long it has been gone.
- last_error: why the node is degraded or down. Built only from fixed
  templates, never raw exception text, and shown only to admins.
- details: per-kind facts. Workers: active_tasks, concurrency,
  pipeline_version. Inference servers: http_status, model_name,
  model_version.
- created_at: when the node was first seen or configured.
- updated_at: when the row last changed.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "name", name="uq_service_nodes_kind_name"),
        sa.CheckConstraint(
            "kind IN ('worker','inference')", name="ck_service_nodes_kind"
        ),
        sa.CheckConstraint(
            "status IN ('healthy','degraded','down','unknown')",
            name="ck_service_nodes_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("service_nodes")
