from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# The tuned pool + PyMySQL connect timeouts are for the production MySQL engine.
# SQLite (used for local dev / tests) rejects those keyword args, so apply them
# only when we're actually talking to MySQL. MySQL behaviour is unchanged.
_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,          # validate connection before use
        pool_recycle=1800,           # recycle connections every 30 min (before MySQL's wait_timeout)
        pool_size=5,
        max_overflow=10,
        connect_args={
            "connect_timeout": 30,
            "read_timeout": 300,     # allow up to 5 min for slow queries (LLM calls)
            "write_timeout": 300,
        },
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
