from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.main import _default_forecast_period
from app.models import Product, Sale
from fastapi.testclient import TestClient


def test_default_forecast_period_starts_today_and_covers_week():
    start, end = _default_forecast_period(date(2026, 6, 18))

    assert start == date(2026, 6, 18)
    assert end == date(2026, 6, 24)


def test_dashboard_counts_group_suffix_products(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    base = Product(onec_id="base", purchase_name="Гвоздика", sales_category="Гвоздика", flower_type="carnation")
    bush = Product(onec_id="bush", purchase_name="Гвоздика кустовая", sales_category="Гвоздика", flower_type="carnation")
    session.add_all([base, bush])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2026, 6, 1), product_id=base.id, quantity=10, revenue=1000, source_row_hash="base-sale"),
            Sale(sale_date=date(2026, 6, 1), product_id=bush.id, quantity=20, revenue=2000, source_row_hash="bush-sale"),
        ]
    )
    session.commit()
    monkeypatch.setenv("MVP_PRODUCT_CATEGORY", "Гвоздика общ")
    import app.main as main

    main.settings.mvp_product_category = "Гвоздика общ"

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    response = TestClient(app).get("/")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<strong>2</strong><span>позиций</span>" in response.text
