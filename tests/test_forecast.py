from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, PurchaseOrder, RecommendationItem, RecommendationRun, Sale, Stock
from app.services.forecast import ForecastItem, build_statistical_forecast, evaluate_against_actual_purchases, save_recommendation_run


def test_statistical_forecast_uses_last_year_sales_and_stock():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="rose_50",
        purchase_name="Роза Эквадор Монблан 50",
        sales_category="Роза 50",
        flower_type="rose",
        variety="Монблан",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 3, 1),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="sale-1",
            ),
            Stock(stock_date=date(2025, 3, 7), product_id=product.id, quantity=20),
            Stock(stock_date=date(2026, 2, 28), product_id=product.id, quantity=20),
            PurchaseOrder(
                order_date=date(2025, 2, 10),
                delivery_date=date(2025, 3, 1),
                product_id=product.id,
                quantity_ordered=120,
                quantity_received=120,
                source_row_hash="purchase-1",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 3, 1), date(2026, 3, 7), "Роза 50")

    assert len(items) == 1
    assert items[0].historical_sold == 100
    assert items[0].historical_leftover == 20
    assert items[0].historical_purchased == 120
    assert items[0].historical_purchase_need == 100
    assert items[0].baseline_demand == 100
    assert items[0].current_stock == 20
    assert items[0].usable_stock == 0
    assert items[0].statistical_quantity == 105
    assert items[0].trend_current_sales == 0
    assert items[0].trend_previous_sales == 0


def test_statistical_forecast_skips_products_without_data():
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
    session.add(
        Sale(
            sale_date=date(2025, 3, 1),
            product_id=used_product.id,
            quantity=10,
            revenue=1000,
            source_row_hash="carnation-sale-1",
        )
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 3, 1), date(2026, 3, 7), "Гвоздика")

    assert [item.product.purchase_name for item in items] == ["Гвоздика красная"]


def test_statistical_forecast_uses_recent_sales_when_same_period_is_empty():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_new",
        purchase_name="Гвоздика МСК",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add(
        Sale(
            sale_date=date(2026, 1, 31),
            product_id=product.id,
            quantity=70,
            revenue=7000,
            source_row_hash="recent-sale",
        )
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 3, 1), date(2026, 3, 7), "Гвоздика")

    assert len(items) == 1
    assert items[0].historical_sold == 0
    assert round(items[0].baseline_demand, 2) == 16.9
    assert items[0].statistical_quantity == 18
    assert "мало данных для анализа" in items[0].explanation


def test_statistical_forecast_does_not_subtract_stock_snapshot_without_receipt_date():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_stock",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 6, 1),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            Stock(stock_date=date(2026, 5, 31), product_id=product.id, quantity=40),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 6, 1), date(2026, 6, 7), "Гвоздика")

    assert items[0].current_stock == 40
    assert items[0].usable_stock == 0
    assert items[0].statistical_quantity == 105


def test_statistical_forecast_ignores_stale_stock_snapshot():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_stale_stock",
        purchase_name="Гвоздика красная",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 5, 2),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            Stock(stock_date=date(2025, 12, 31), product_id=product.id, quantity=500),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика")

    assert items[0].current_stock == 0
    assert items[0].stock_snapshot_date is None
    assert items[0].stock_age_days > 14


def test_statistical_forecast_filters_by_exact_product_or_explicit_group():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    base = Product(onec_id="base", purchase_name="Гвоздика", sales_category="Гвоздика", flower_type="carnation")
    bush = Product(onec_id="bush", purchase_name="Гвоздика кустовая", sales_category="Гвоздика", flower_type="carnation")
    session.add_all([base, bush])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2025, 5, 2), product_id=base.id, quantity=10, revenue=1000, source_row_hash="base-sale"),
            Sale(sale_date=date(2025, 5, 2), product_id=bush.id, quantity=20, revenue=2000, source_row_hash="bush-sale"),
        ]
    )
    session.commit()

    base_items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика")
    group_items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика общ")
    bush_items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "гвоздика кустовая")

    assert [item.product.purchase_name for item in base_items] == ["Гвоздика"]
    assert {item.product.purchase_name for item in group_items} == {"Гвоздика", "Гвоздика кустовая"}
    assert [item.product.purchase_name for item in bush_items] == ["Гвоздика кустовая"]


def test_statistical_forecast_exact_product_name_has_priority_over_token_search():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    red = Product(onec_id="red", purchase_name="Гвоздика красная", sales_category="Гвоздика", flower_type="carnation")
    red_msk = Product(
        onec_id="red_msk",
        purchase_name="Гвоздика красная МСК",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add_all([red, red_msk])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2025, 5, 2), product_id=red.id, quantity=10, revenue=1000, source_row_hash="red-sale"),
            Sale(
                sale_date=date(2025, 5, 2),
                product_id=red_msk.id,
                quantity=20,
                revenue=2000,
                source_row_hash="red-msk-sale",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика красная")

    assert [item.product.purchase_name for item in items] == ["Гвоздика красная"]


def test_statistical_forecast_group_suffix_selects_whole_category_when_product_has_same_name():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    base = Product(onec_id="base", purchase_name="Гвоздика", sales_category="Гвоздика", flower_type="carnation")
    bush = Product(onec_id="bush", purchase_name="Гвоздика кустовая", sales_category="Гвоздика", flower_type="carnation")
    session.add_all([base, bush])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2025, 5, 2), product_id=base.id, quantity=10, revenue=1000, source_row_hash="base-sale"),
            Sale(sale_date=date(2025, 5, 2), product_id=bush.id, quantity=20, revenue=2000, source_row_hash="bush-sale"),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика общ")

    assert {item.product.purchase_name for item in items} == {"Гвоздика", "Гвоздика кустовая"}


def test_statistical_forecast_exact_base_product_does_not_select_whole_category():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    base = Product(onec_id="base", purchase_name="Гвоздика", sales_category="Гвоздика", flower_type="carnation")
    bush = Product(onec_id="bush", purchase_name="Гвоздика кустовая", sales_category="Гвоздика", flower_type="carnation")
    session.add_all([base, bush])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2025, 5, 2), product_id=base.id, quantity=10, revenue=1000, source_row_hash="base-sale"),
            Sale(sale_date=date(2025, 5, 2), product_id=bush.id, quantity=20, revenue=2000, source_row_hash="bush-sale"),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 2), date(2026, 5, 9), "Гвоздика")

    assert [item.product.purchase_name for item in items] == ["Гвоздика"]


def test_bare_category_name_does_not_select_whole_group_without_common_suffix():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    kimberly = Product(onec_id="kimberly", purchase_name="Кимберли 60", sales_category="Роза", flower_type="rose")
    naomi = Product(onec_id="naomi", purchase_name="Вайт наоми 50", sales_category="Роза", flower_type="rose")
    session.add_all([kimberly, naomi])
    session.flush()
    session.add_all(
        [
            Sale(sale_date=date(2026, 6, 1), product_id=kimberly.id, quantity=10, revenue=1000, source_row_hash="k"),
            Sale(sale_date=date(2026, 6, 1), product_id=naomi.id, quantity=20, revenue=2000, source_row_hash="n"),
        ]
    )
    session.commit()

    bare_items = build_statistical_forecast(session, date(2026, 6, 2), date(2026, 6, 8), "Роза")
    group_items = build_statistical_forecast(session, date(2026, 6, 2), date(2026, 6, 8), "Роза общ")

    assert bare_items == []
    assert {item.product.purchase_name for item in group_items} == {"Кимберли 60", "Вайт наоми 50"}


def test_statistical_forecast_subtracts_incoming_flowers_delivered_within_shelf_life():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_incoming",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 6, 1),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            PurchaseOrder(
                order_date=date(2026, 5, 10),
                delivery_date=date(2026, 6, 2),
                product_id=product.id,
                quantity_ordered=40,
                quantity_received=40,
                source_row_hash="fresh-incoming",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 6, 1), date(2026, 6, 7), "Гвоздика")

    assert items[0].incoming_orders == 40
    assert items[0].statistical_quantity == 65


def test_statistical_forecast_subtracts_only_fresh_stock_confirmed_by_recent_receipts():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_fresh_stock",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 6, 1),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            Stock(stock_date=date(2026, 5, 31), product_id=product.id, quantity=4000),
            PurchaseOrder(
                order_date=date(2026, 5, 20),
                delivery_date=date(2026, 5, 30),
                product_id=product.id,
                quantity_ordered=40,
                quantity_received=40,
                source_row_hash="recent-receipt",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 6, 1), date(2026, 6, 7), "Гвоздика")

    assert items[0].current_stock == 4000
    assert items[0].usable_stock == 40
    assert items[0].statistical_quantity == 65


def test_statistical_forecast_subtracts_fresh_stock_available_at_target_start():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_receipt_rhythm",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 4, 1),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            Stock(stock_date=date(2026, 3, 31), product_id=product.id, quantity=1000),
            PurchaseOrder(
                order_date=date(2026, 3, 21),
                delivery_date=date(2026, 3, 21),
                product_id=product.id,
                quantity_ordered=1000,
                quantity_received=1000,
                source_row_hash="receipt-1",
            ),
            PurchaseOrder(
                order_date=date(2026, 3, 28),
                delivery_date=date(2026, 3, 28),
                product_id=product.id,
                quantity_ordered=1000,
                quantity_received=1000,
                source_row_hash="receipt-2",
            ),
        ]
    )
    for day in range(2, 31):
        session.add(
            Sale(
                sale_date=date(2026, 3, day),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash=f"recent-sale-{day}",
            )
        )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 4, 1), date(2026, 4, 8), "Гвоздика")

    assert items[0].usable_stock == 1000
    assert items[0].statistical_quantity == 0


def test_statistical_forecast_does_not_use_future_snapshots_for_future_target():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    today = date.today()
    target_start = today + timedelta(days=30)
    target_end = target_start + timedelta(days=6)
    product = Product(
        onec_id="carnation_future",
        purchase_name="Гвоздика будущая",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=today + timedelta(days=5),
                product_id=product.id,
                quantity=1000,
                revenue=100000,
                source_row_hash="future-sale",
            ),
            Stock(stock_date=today + timedelta(days=5), product_id=product.id, quantity=4000),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, target_start, target_end, "Гвоздика")

    assert len(items) == 1
    assert items[0].trend_current_sales == 0
    assert items[0].current_stock == 0
    assert items[0].usable_stock == 0
    assert items[0].statistical_quantity == 0


def test_calendar_event_fallback_adjusts_carnation_recommendation():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_victory_day",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add(
        Sale(
            sale_date=date(2025, 5, 9),
            product_id=product.id,
            quantity=100,
            revenue=10000,
            source_row_hash="hist-sale",
        )
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 8), date(2026, 5, 10), "Гвоздика")
    run = save_recommendation_run(
        session,
        target_start=date(2026, 5, 8),
        target_end=date(2026, 5, 10),
        category="Гвоздика",
        items=items,
        primary_ai={"primary_items": []},
        event_ai={
            "events": [{"name": "День Победы"}],
            "event_items": [],
        },
    )

    assert run.items[0].final_quantity == 111
    assert "День Победы" in run.items[0].explanation


def test_calendar_event_does_not_adjust_when_history_shows_large_leftover():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="carnation_victory_day_leftover",
        purchase_name="Гвоздика",
        sales_category="Гвоздика",
        flower_type="carnation",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2025, 5, 9),
                product_id=product.id,
                quantity=100,
                revenue=10000,
                source_row_hash="hist-sale",
            ),
            Stock(stock_date=date(2025, 5, 10), product_id=product.id, quantity=50),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 5, 8), date(2026, 5, 10), "Гвоздика")
    run = save_recommendation_run(
        session,
        target_start=date(2026, 5, 8),
        target_end=date(2026, 5, 10),
        category="Гвоздика",
        items=items,
        primary_ai={"primary_items": []},
        event_ai={"events": [{"name": "День Победы"}], "event_items": []},
    )

    assert run.items[0].final_quantity == run.items[0].statistical_quantity
    assert "прибавка не применена" in run.items[0].explanation


def test_purchase_metrics_skip_empty_rows_and_do_not_fake_ape_for_zero_actual():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    used_product = Product(onec_id="used", purchase_name="Гвоздика", sales_category="Гвоздика")
    empty_product = Product(onec_id="empty", purchase_name="Гвоздика пустая", sales_category="Гвоздика")
    session.add_all([used_product, empty_product])
    session.flush()
    run = RecommendationRun(
        target_start_date=date(2026, 5, 1),
        target_end_date=date(2026, 5, 7),
        category="Гвоздика",
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            PurchaseOrder(
                order_date=date(2026, 4, 1),
                delivery_date=date(2026, 5, 2),
                product_id=used_product.id,
                quantity_ordered=0,
                quantity_received=0,
                source_row_hash="zero-purchase",
            ),
        ]
    )
    session.add_all(
        [
            RecommendationItem(
                run_id=run.id,
                product_id=used_product.id,
                statistical_quantity=100,
                ai_quantity=100,
                final_quantity=100,
                current_stock=0,
                incoming_orders=0,
                baseline_demand=100,
                trend_coefficient=1,
                safety_stock=0,
            ),
            RecommendationItem(
                run_id=run.id,
                product_id=empty_product.id,
                statistical_quantity=0,
                ai_quantity=0,
                final_quantity=0,
                current_stock=0,
                incoming_orders=0,
                baseline_demand=0,
                trend_coefficient=1,
                safety_stock=0,
            ),
        ]
    )
    session.commit()

    rows = evaluate_against_actual_purchases(session, run)

    assert len(rows) == 1
    assert rows[0]["absolute_percentage_error"] is None


def test_short_history_uses_observed_sales_window_instead_of_full_lookback():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(
        onec_id="new_rose",
        purchase_name="Вайт наоми 50",
        sales_category="Роза",
        flower_type="rose",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=date(2026, 6, 10),
                product_id=product.id,
                quantity=10,
                revenue=1000,
                source_row_hash="new-sale-1",
            ),
            Sale(
                sale_date=date(2026, 6, 11),
                product_id=product.id,
                quantity=10,
                revenue=1000,
                source_row_hash="new-sale-2",
            ),
            Sale(
                sale_date=date(2026, 6, 12),
                product_id=product.id,
                quantity=10,
                revenue=1000,
                source_row_hash="new-sale-3",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, date(2026, 6, 13), date(2026, 6, 19), "Роза")

    assert len(items) == 1
    assert items[0].historical_sold == 0
    assert items[0].baseline_demand == 70
    assert items[0].statistical_quantity == 74
    assert "мало данных для анализа" in items[0].explanation
    assert "нет истории за прошлые годы" in items[0].explanation


def test_short_history_does_not_subtract_stock_that_will_not_survive_target_period():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    today = date.today()
    product = Product(
        onec_id="kimberly_60",
        purchase_name="Кимберли 60",
        sales_category="Роза",
        flower_type="rose",
    )
    session.add(product)
    session.flush()
    for index in range(7):
        session.add(
            Sale(
                sale_date=today - timedelta(days=6 - index),
                product_id=product.id,
                quantity=75 / 7,
                revenue=1000,
                source_row_hash=f"kimberly-sale-{index}",
            )
        )
    session.add_all(
        [
            Stock(stock_date=today - timedelta(days=1), product_id=product.id, quantity=200),
            PurchaseOrder(
                order_date=today - timedelta(days=1),
                delivery_date=today - timedelta(days=1),
                product_id=product.id,
                quantity_ordered=200,
                quantity_received=200,
                source_row_hash="kimberly-receipt",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, today + timedelta(days=21), today + timedelta(days=27), "Кимберли 60")

    assert len(items) == 1
    assert round(items[0].baseline_demand) == 75
    assert items[0].current_stock == 200
    assert items[0].usable_stock == 0
    assert items[0].statistical_quantity == 79
    assert "тренд не рассчитывается" in items[0].explanation


def test_short_history_uses_month_over_month_trend_before_yearly_history_exists():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    today = date.today()
    target_start = today + timedelta(days=7)
    product = Product(
        onec_id="new_monthly_trend",
        purchase_name="Кимберли 60",
        sales_category="Роза",
        flower_type="rose",
    )
    session.add(product)
    session.flush()
    session.add_all(
        [
            Sale(
                sale_date=today - timedelta(days=45),
                product_id=product.id,
                quantity=60,
                revenue=6000,
                source_row_hash="previous-month-sale",
            ),
            Sale(
                sale_date=today - timedelta(days=5),
                product_id=product.id,
                quantity=120,
                revenue=12000,
                source_row_hash="current-month-sale",
            ),
        ]
    )
    session.commit()

    items = build_statistical_forecast(session, target_start, target_start + timedelta(days=6), "Кимберли 60")

    assert len(items) == 1
    assert items[0].historical_sold == 0
    assert items[0].trend_coefficient > 1
    assert items[0].baseline_demand > 28
    assert "месячный тренд" in items[0].explanation


def test_short_history_splits_available_history_when_less_than_two_months():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    today = date.today()
    target_start = today + timedelta(days=7)
    product = Product(
        onec_id="new_split_trend",
        purchase_name="Бомбастик куст. 50",
        sales_category="Роза",
        flower_type="rose",
    )
    session.add(product)
    session.flush()
    for offset in range(34):
        quantity = 5 if offset < 17 else 15
        session.add(
            Sale(
                sale_date=today - timedelta(days=33 - offset),
                product_id=product.id,
                quantity=quantity,
                revenue=quantity * 100,
                source_row_hash=f"split-sale-{offset}",
            )
        )
    session.commit()

    items = build_statistical_forecast(session, target_start, target_start + timedelta(days=6), "Бомбастик куст. 50")

    assert len(items) == 1
    assert items[0].trend_coefficient > 1
    assert items[0].trend_previous_sales > 0
    assert "месячный тренд" in items[0].explanation


def test_ai_quantity_self_check_keeps_statistical_quantity_when_ai_returns_zero():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    product = Product(onec_id="ai_check", purchase_name="Кимберли 60", sales_category="Роза", flower_type="rose")
    session.add(product)
    session.flush()
    session.commit()
    forecast_item = ForecastItem(
        product=product,
        statistical_quantity=50,
        current_stock=0,
        usable_stock=0,
        stock_snapshot_date=None,
        stock_age_days=None,
        historical_sold=0,
        historical_leftover=0,
        historical_purchased=0,
        historical_purchase_need=0,
        incoming_orders=0,
        expected_next_receipt_date=None,
        expected_next_receipt_note="нет данных",
        baseline_demand=48,
        trend_coefficient=1,
        trend_current_sales=48,
        trend_previous_sales=0,
        trend_current_start=date.today() - timedelta(days=6),
        trend_current_end=date.today(),
        trend_previous_start=date.today() - timedelta(days=36),
        trend_previous_end=date.today() - timedelta(days=7),
        short_history_first_sale_date=date.today() - timedelta(days=6),
        short_history_days=7,
        short_history_last_7_sales=48,
        short_history_last_30_sales=48,
        short_history_weekly_average=48,
        short_history_monthly_average=48,
        short_history_period_average=48,
        safety_stock=2,
        explanation="test",
    )

    saved = save_recommendation_run(
        session,
        target_start=date.today(),
        target_end=date.today() + timedelta(days=6),
        category="Роза",
        items=[forecast_item],
        primary_ai={"primary_items": [{"purchase_name": "Кимберли 60", "recommended_quantity": 0, "recommendation_text": "0"}]},
    )

    assert saved.items[0].final_quantity == 50
    assert "AI вернул 0" in saved.items[0].explanation
