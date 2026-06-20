from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if settings.database_url.startswith("sqlite:///./"):
        db_path = Path(settings.database_url.replace("sqlite:///./", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    from app.services.admin import ensure_default_admin

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()


def _apply_lightweight_migrations() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "recommendation_items" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("recommendation_items")}
    with engine.begin() as connection:
        if "historical_purchased" not in columns:
            connection.execute(
                text("ALTER TABLE recommendation_items ADD COLUMN historical_purchased FLOAT DEFAULT 0")
            )
        for column_name, column_type in {
            "trend_current_sales": "FLOAT DEFAULT 0",
            "trend_previous_sales": "FLOAT DEFAULT 0",
            "trend_current_start": "DATE",
            "trend_current_end": "DATE",
            "trend_previous_start": "DATE",
            "trend_previous_end": "DATE",
            "short_history_first_sale_date": "DATE",
            "short_history_days": "INTEGER DEFAULT 0",
            "short_history_last_7_sales": "FLOAT DEFAULT 0",
            "short_history_last_30_sales": "FLOAT DEFAULT 0",
            "short_history_weekly_average": "FLOAT DEFAULT 0",
            "short_history_monthly_average": "FLOAT DEFAULT 0",
            "short_history_period_average": "FLOAT DEFAULT 0",
            "expected_next_receipt_date": "DATE",
            "expected_next_receipt_note": "VARCHAR(255) DEFAULT ''",
        }.items():
            if column_name not in columns:
                connection.execute(text(f"ALTER TABLE recommendation_items ADD COLUMN {column_name} {column_type}"))
