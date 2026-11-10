"""partner api + secure candidate documents

- New rec_* tables: candidate documents, API keys, idempotency records,
  request logs, webhook endpoints/deliveries, integration audit.
- New columns: rec_agencies.partner_api_access / partner_api_rate_limit /
  public_job_feed_enabled; rec_roles.external_ref / source;
  rec_clients.external_ref; rec_applications source/intake/processing fields.
- MySQL: extend the candidate-source and notification-kind ENUMs.
- Backfill: legacy CV files (raw disk paths in source_file) are moved into the
  StorageService as rec_candidate_documents rows; source_file keeps only the
  bare filename.

Idempotent. Stays inside the rec_* data wall — no consumer tables touched.

Revision ID: 0035_partner_api
Revises: 0034_pipeline_unification
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa


revision = "0035_partner_api"
down_revision = "0034_pipeline_unification"
branch_labels = None
depends_on = None



def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _add(insp, table, column):
    if insp.has_table(table) and column.name not in _cols(insp, table):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    _add(insp, "rec_agencies", sa.Column("partner_api_access", sa.String(16), nullable=False, server_default="plan"))
    _add(insp, "rec_agencies", sa.Column("partner_api_rate_limit", sa.Integer, nullable=True))
    _add(insp, "rec_agencies", sa.Column("public_job_feed_enabled", sa.Boolean, nullable=False, server_default=sa.false()))

    _add(insp, "rec_roles", sa.Column("external_ref", sa.String(128), nullable=True))
    _add(insp, "rec_roles", sa.Column("source", sa.String(20), nullable=False, server_default="app"))
    _add(insp, "rec_clients", sa.Column("external_ref", sa.String(128), nullable=True))

    for col in (
        sa.Column("source", sa.String(20), nullable=False, server_default="app"),
        sa.Column("source_detail", sa.JSON, nullable=True),
        sa.Column("external_ref", sa.String(128), nullable=True),
        sa.Column("processing_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("processing_error", sa.String(300), nullable=True),
        sa.Column("form_answers", sa.JSON, nullable=True),
        sa.Column("cover_letter", sa.Text, nullable=True),
        sa.Column("intake_api_key_id", sa.Integer, nullable=True),
        sa.Column("intake_client_id", sa.Integer, nullable=True),
        sa.Column("match_explanation", sa.JSON, nullable=True),
    ):
        _add(insp, "rec_applications", col)

    insp = sa.inspect(bind)
    existing_ix = {
        t: {i["name"] for i in insp.get_indexes(t)} for t in ("rec_roles", "rec_clients", "rec_applications")
        if insp.has_table(t)
    }
    for table, name, cols in (
        ("rec_roles", "ix_rec_roles_external_ref", ["external_ref"]),
        ("rec_clients", "ix_rec_clients_external_ref", ["external_ref"]),
        ("rec_applications", "ix_rec_applications_external_ref", ["external_ref"]),
        ("rec_applications", "ix_rec_applications_intake_api_key_id", ["intake_api_key_id"]),
        ("rec_applications", "ix_rec_applications_intake_client_id", ["intake_client_id"]),
    ):
        if table in existing_ix and name not in existing_ix[table]:
            op.create_index(name, table, cols)
    if "rec_roles" in existing_ix and "uq_rec_role_external_ref" not in existing_ix["rec_roles"]:
        uqs = {u["name"] for u in insp.get_unique_constraints("rec_roles")}
        if "uq_rec_role_external_ref" not in uqs and bind.dialect.name != "sqlite":
            op.create_unique_constraint("uq_rec_role_external_ref", "rec_roles", ["agency_id", "external_ref"])

    # New tables — created from the models so DDL stays in one place.
    from app.recruiter.models import (
        ApiKey, ApiRequestLog, CandidateDocument, IdempotencyRecord, IntegrationAuditEvent,
        WebhookDelivery, WebhookEndpoint,
    )
    for model in (CandidateDocument, ApiKey, IdempotencyRecord, ApiRequestLog,
                  WebhookEndpoint, WebhookDelivery, IntegrationAuditEvent):
        model.__table__.create(bind=bind, checkfirst=True)

    if bind.dialect.name == "mysql":
        from app.recruiter.enums import CandidateSource, NotificationKind

        vals = ", ".join(f"'{v.value}'" for v in CandidateSource)
        op.execute(
            f"ALTER TABLE rec_candidate_profiles MODIFY COLUMN source ENUM({vals}) NOT NULL DEFAULT 'bulk_cv'"
        )
        kinds = ", ".join(f"'{k.value}'" for k in NotificationKind)
        op.execute(f"ALTER TABLE rec_notifications MODIFY COLUMN kind ENUM({kinds}) NOT NULL")

    # Legacy CV files → StorageService (best-effort; never fails the migration).
    try:
        from sqlalchemy.orm import Session

        from app.recruiter.services.legacy_files import backfill_legacy_cv_files

        with Session(bind=bind) as s:
            backfill_legacy_cv_files(s)
    except Exception as exc:  # pragma: no cover
        print(f"[0035] legacy CV backfill skipped: {exc}")


def downgrade() -> None:
    # Data-bearing tables are intentionally not dropped on downgrade.
    pass
