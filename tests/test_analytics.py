from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, Sale, Stock
from app.services.importer import _as_date
from app.services.analytics import build_analytics_data, build_backtest


def test_backtest_skips_rows_without_any_report_data():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    used_product = Product(
        onec_id="carnation_used",
        purchase_name="Гвоздика красная",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    empty_product = Product(
        onec_id="carnation_empty",
        purchase_name="Гвоздика пустая",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add_all([used_product, empty_product])
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 6, 1),
                product_id=used_product.id,
                quantity=10,
                revenue=1000,
                source_row_hash="last-year-sale",
            ),
            Sale(
                sale_date=date(2026, 6, 1),
                product_id=used_product.id,
                quantity=12,
                revenue=1200,
                source_row_hash="actual-sale",
            ),
            Stock(stock_date=date(2026, 6, 7), product_id=used_product.id, quantity=3),
        ]
    )
    session.commit()

    result = build_backtest(session, date(2026, 6, 1), date(2026, 6, 7), "Гвоздика")

    assert [row.product_name for row in result.rows] == ["Гвоздика красная"]
    assert result.total_actual_sales == 12
    assert result.total_actual_leftover == 3
    assert result.rows[0].opening_stock == 0
    assert result.rows[0].recommended_leftover == 0
    assert result.rows[0].recommended_shortage == 1
    assert result.rows[0].actual_modeled_leftover == 0
    assert result.rows[0].actual_balance_gap == 3


def test_import_date_parser_keeps_iso_dates_in_year_month_day_order():
    assert _as_date("2026-02-09") == date(2026, 2, 9)
    assert _as_date("09.02.2026 0:00:00") == date(2026, 2, 9)


def test_analytics_does_not_include_store_sales_without_store_source_data():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_used",
        purchase_name="Гвоздика красная",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add(
        Sale(
            sale_date=date(2026, 6, 1),
            product_id=product.id,
            quantity=12,
            revenue=1200,
            source_row_hash="actual-sale",
        )
    )
    session.commit()

    data = build_analytics_data(session, "Гвоздика", date(2026, 6, 1), date(2026, 6, 7))

    assert "store_sales" not in data
    assert "stores_count" not in data["summary"]


def test_analytics_group_suffix_uses_underlying_category():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    base = Product(onec_id="base", purchase_name="Гвоздика", sales_category="Гвоздика", flower_type="carnation")
    bush = Product(onec_id="bush", purchase_name="Гвоздика кустовая", sales_category="Гвоздика", flower_type="carnation")
    session.add_all([base, bush])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2026, 6, 1), product_id=base.id, quantity=10, revenue=1000, source_row_hash="base-sale"),
            Sale(sale_date=date(2026, 6, 1), product_id=bush.id, quantity=20, revenue=2000, source_row_hash="bush-sale"),
            Stock(stock_date=date(2026, 6, 1), product_id=base.id, quantity=5),
            Stock(stock_date=date(2026, 6, 1), product_id=bush.id, quantity=7),
        ]
    )
    session.commit()

    data = build_analytics_data(session, "Гвоздика общ", date(2026, 6, 1), date(2026, 6, 1))

    assert data["summary"]["sales_total"] == 30
    assert data["summary"]["stock_latest"] == 12


def test_backtest_marks_missing_end_stock_snapshot_as_unknown():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_used",
        purchase_name="Гвоздика красная",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Stock(stock_date=date(2026, 2, 1), product_id=product.id, quantity=100),
            Stock(stock_date=date(2026, 2, 13), product_id=product.id, quantity=50),
            Sale(
                sale_date=date(2026, 2, 3),
                product_id=product.id,
                quantity=20,
                revenue=2000,
                source_row_hash="actual-sale",
            ),
        ]
    )
    session.commit()

    result = build_backtest(session, date(2026, 2, 2), date(2026, 2, 9), "Гвоздика")

    assert result.stock_snapshot_on_end is False
    assert result.nearest_stock_before_end == date(2026, 2, 1)
    assert result.nearest_stock_after_end == date(2026, 2, 13)
    assert result.total_actual_leftover == 80
    assert result.total_actual_balance_gap == 0
    assert result.rows[0].actual_leftover_source == "расчет от 2026-02-01"
