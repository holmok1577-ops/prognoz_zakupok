from datetime import date
from pathlib import Path
from urllib.parse import unquote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.main import _default_forecast_period
from app.models import Product, PurchaseOrder, Sale, Stock
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


def test_dashboard_counts_all_products_with_data_and_keeps_forecast_input_empty(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    rose = Product(onec_id="rose", purchase_name="Роза Эквадор 50", sales_category="Роза", flower_type="rose")
    carnation = Product(
        onec_id="carnation",
        purchase_name="Гвоздика красная",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    empty = Product(onec_id="empty", purchase_name="Пустая позиция", sales_category="Роза", flower_type="rose")
    session.add_all([rose, carnation, empty])
    session.flush()
    session.add(Sale(sale_date=date(2026, 6, 1), product_id=rose.id, quantity=10, revenue=1000, source_row_hash="sale"))
    session.add(Stock(stock_date=date(2026, 6, 1), product_id=carnation.id, quantity=20))
    session.add(
        PurchaseOrder(
            order_date=date(2026, 5, 20),
            delivery_date=date(2026, 6, 1),
            product_id=carnation.id,
            quantity_ordered=20,
            quantity_received=20,
            source_row_hash="receipt",
        )
    )
    session.commit()
    monkeypatch.setenv("MVP_PRODUCT_CATEGORY", "Роза")
    import app.main as main

    main.settings.mvp_product_category = "Роза"

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
    assert 'placeholder="Начните вводить название номенклатуры"' in response.text
    assert 'value="Роза"' not in response.text
    assert "Импорт CSV" not in response.text
    assert "/analytics?category=Роза общ" in response.text
    assert "/backtest?category=Роза общ" in response.text


def test_analytics_category_input_is_empty_with_product_suggestions(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    product = Product(onec_id="rose", purchase_name="Роза Эквадор 50", sales_category="Роза", flower_type="rose")
    session.add(product)
    session.flush()
    session.add(Sale(sale_date=date(2026, 6, 1), product_id=product.id, quantity=10, revenue=1000, source_row_hash="sale"))
    session.commit()
    monkeypatch.setenv("MVP_PRODUCT_CATEGORY", "Роза")
    import app.main as main

    main.settings.mvp_product_category = "Роза"

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    response = TestClient(app).get("/analytics")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Аналитика продаж" in response.text
    assert "Роза общ за" in response.text
    assert 'name="category"' in response.text
    assert 'placeholder="Начните вводить название номенклатуры"' in response.text
    assert 'value="Роза"' not in response.text
    assert '<option value="Роза Эквадор 50"></option>' in response.text
    assert '<option value="Роза общ"></option>' in response.text


def test_backtest_category_input_is_empty_with_product_suggestions(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    product = Product(onec_id="rose", purchase_name="Роза Эквадор 50", sales_category="Роза", flower_type="rose")
    session.add(product)
    session.flush()
    session.add(Sale(sale_date=date(2026, 6, 1), product_id=product.id, quantity=10, revenue=1000, source_row_hash="sale"))
    session.commit()
    monkeypatch.setenv("MVP_PRODUCT_CATEGORY", "Роза")
    import app.main as main

    main.settings.mvp_product_category = "Роза"

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    response = TestClient(app).get("/backtest")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Проверка на истории" in response.text
    assert "Роза общ за" in response.text
    assert 'name="category"' in response.text
    assert 'placeholder="Начните вводить название номенклатуры"' in response.text
    assert 'value="Роза"' not in response.text
    assert '<option value="Роза Эквадор 50"></option>' in response.text
    assert '<option value="Роза общ"></option>' in response.text


def test_admin_auth_redirects_to_login_and_allows_login(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    import app.main as main
    import app.services.admin as admin

    backup_dir = Path(".tmp/test-admin-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(main.settings, "admin_auth_enabled", True)
    monkeypatch.setattr(main.settings, "admin_username", "admin")
    monkeypatch.setattr(main.settings, "admin_initial_password", "secret123")
    monkeypatch.setattr(main.settings, "admin_backup_dir", str(backup_dir))
    monkeypatch.setattr(admin.settings, "admin_auth_enabled", True)
    monkeypatch.setattr(admin.settings, "admin_username", "admin")
    monkeypatch.setattr(admin.settings, "admin_initial_password", "secret123")
    monkeypatch.setattr(admin.settings, "admin_backup_dir", str(backup_dir))

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "secret123", "next": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "flower_admin_session" in response.headers.get("set-cookie", "")

    response = client.get("/")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Админка" in response.text
    assert "Выйти" in response.text
    assert "return confirm('Вы уверены, что хотите очистить базу данных?" in response.text


def test_admin_change_password_requires_repeated_password(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    import app.main as main
    import app.services.admin as admin

    backup_dir = Path(".tmp/test-admin-backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(main.settings, "admin_auth_enabled", True)
    monkeypatch.setattr(main.settings, "admin_username", "admin")
    monkeypatch.setattr(main.settings, "admin_initial_password", "secret123")
    monkeypatch.setattr(main.settings, "admin_backup_dir", str(backup_dir))
    monkeypatch.setattr(admin.settings, "admin_auth_enabled", True)
    monkeypatch.setattr(admin.settings, "admin_username", "admin")
    monkeypatch.setattr(admin.settings, "admin_initial_password", "secret123")
    monkeypatch.setattr(admin.settings, "admin_backup_dir", str(backup_dir))

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": "admin", "password": "secret123", "next": "/admin"},
        follow_redirects=False,
    )
    response = client.post(
        "/admin/change-password",
        data={
            "current_password": "secret123",
            "new_password": "newsecret123",
            "repeated_password": "newsecret124",
        },
        follow_redirects=False,
    )
    app.dependency_overrides.clear()

    assert response.status_code == 303
    assert "Новый пароль и повтор пароля не совпадают" in unquote_plus(response.headers["location"])
