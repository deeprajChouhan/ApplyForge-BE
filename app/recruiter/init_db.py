"""
Create the recruiter module's tables without touching the consumer schema.

Uses `checkfirst=True` so it only creates missing `rec_` tables and is safe to
run on every boot. Once the schema stabilises, fold these into the app's Alembic
migrations like the rest of the models.
"""
from app.db.session import engine
from app.recruiter.models import RECRUITER_TABLES


def ensure_recruiter_tables() -> None:
    for table in RECRUITER_TABLES:
        table.create(bind=engine, checkfirst=True)
