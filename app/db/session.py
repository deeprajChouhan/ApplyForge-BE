from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

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
