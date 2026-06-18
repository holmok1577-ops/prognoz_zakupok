from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, PurchaseOrder, Sale, Stock, Store
from app.services.forecast import ForecastItem, build_statistical_forecast, _products_for_query


@dataclass(frozen=True)
class BacktestRow:
    product_name: str
    recommended: float
    actual_purchase: float
    actual_sales: float
    opening_stock: float
    opening_stock_source: str
    actual_leftover: float | None
    actual_leftover_source: str
    recommended_leftover: float
    recommended_shortage: float
    actual_modeled_leftover: float
    actual_balance_gap: float | None
    actual_flow_gap: float | None
    purchase_error: float
    sales_error: float
    purchase_ape: float | None
    sales_ape: float | None
    historical_sold: float
    historical_leftover: float
    historical_purchased: float
    trend_coefficient: float
    explanation: str


@dataclass(frozen=True)
class BacktestResult:
    rows: list[BacktestRow]
    total_recommended: float
    total_actual_purchase: float
    total_actual_sales: float
    total_opening_stock: float
    total_actual_leftover: float | None
    total_recommended_leftover: float
    total_recommended_shortage: float
    total_actual_modeled_leftover: float
    total_actual_balance_gap: float | None
    total_actual_flow_gap: float | None
    purchase_mape: float | None
    sales_mape: float | None
    purchase_rmse: float
    sales_rmse: float
    stock_snapshot_on_start: bool
    stock_snapshot_on_end: bool
    nearest_stock_before_end: date | None
    nearest_stock_after_end: date | None


def _product_ids_for_category(db: Session, category: str) -> list[int]:
    return [product.id for product in _products_for_query(db, category)]


def _has_stock_snapshot_for_category(db: Session, product_ids: list[int], stock_date: date) -> bool:
    if not product_ids:
        return False
    return bool(
        db.scalar(
            select(func.count(Stock.id)).where(
                Stock.product_id.in_(product_ids),
                Stock.stock_date == stock_date,
            )
        )
    )


def _nearest_stock_snapshot_dates(db: Session, product_ids: list[int], target_date: date) -> tuple[date | None, date | None]:
    if not product_ids:
        return None, None
    before = db.scalar(
        select(func.max(Stock.stock_date)).where(
            Stock.product_id.in_(product_ids),
            Stock.stock_date < target_date,
        )
    )
    after = db.scalar(
        select(func.min(Stock.stock_date)).where(
            Stock.product_id.in_(product_ids),
            Stock.stock_date > target_date,
        )
    )
    return before, after


def _sum_actual_sales(db: Session, product_id: int, start: date, end: date) -> float:
    return float(
        db.scalar(
            select(func.coalesce(func.sum(Sale.quantity), 0)).where(
                Sale.product_id == product_id,
                Sale.sale_date >= start,
                Sale.sale_date <= end,
            )
        )
        or 0
    )


def _sum_actual_purchase(db: Session, product_id: int, start: date, end: date) -> float:
    return float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0)).where(
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.delivery_date >= start,
                PurchaseOrder.delivery_date <= end,
            )
        )
        or 0
    )


def _quantities_by_date(db: Session, model, product_id: int, date_column, quantity_column, start: date, end: date) -> dict[date, float]:
    return {
        row.day: float(row.quantity or 0)
        for row in db.execute(
            select(date_column.label("day"), func.coalesce(func.sum(quantity_column), 0).label("quantity"))
            .where(
                model.product_id == product_id,
                date_column >= start,
                date_column <= end,
            )
            .group_by(date_column)
        )
    }


def _stock_on_date(db: Session, product_id: int, stock_date: date) -> float | None:
    rows_count = db.scalar(
        select(func.count(Stock.id)).where(
            Stock.product_id == product_id,
            Stock.stock_date == stock_date,
        )
    )
    if not rows_count:
        return None
    return float(
        db.scalar(
            select(func.coalesce(func.sum(Stock.quantity), 0)).where(
                Stock.product_id == product_id,
                Stock.stock_date == stock_date,
            )
        )
        or 0
    )


def _nearest_stock_snapshot_before_or_on(db: Session, product_id: int, stock_date: date) -> tuple[date | None, float]:
    snapshot_date = db.scalar(
        select(func.max(Stock.stock_date)).where(
            Stock.product_id == product_id,
            Stock.stock_date <= stock_date,
        )
    )
    if snapshot_date is None:
        return None, 0.0
    return snapshot_date, _stock_on_date(db, product_id, snapshot_date) or 0.0


def _reconstruct_stock_on_date(db: Session, product_id: int, stock_date: date) -> tuple[float, str]:
    exact_stock = _stock_on_date(db, product_id, stock_date)
    if exact_stock is not None:
        return exact_stock, "снимок"
    snapshot_date, snapshot_stock = _nearest_stock_snapshot_before_or_on(db, product_id, stock_date)
    if snapshot_date is None:
        return 0.0, "нет данных"
    if snapshot_date == stock_date:
        return snapshot_stock, "снимок"
    sales = _sum_actual_sales(db, product_id, snapshot_date + timedelta(days=1), stock_date)
    purchases = _sum_actual_purchase(db, product_id, snapshot_date + timedelta(days=1), stock_date)
    reconstructed = max(snapshot_stock + purchases - sales, 0)
    return reconstructed, f"расчет от {snapshot_date.isoformat()}"


def _simulate_leftover_and_shortage(opening_stock: float, incoming: dict[date, float], sales: dict[date, float], start: date, end: date) -> tuple[float, float]:
    balance = opening_stock
    shortage = 0.0
    for day in _date_range(start, end):
        balance += incoming.get(day, 0)
        balance -= sales.get(day, 0)
        if balance < 0:
            shortage += abs(balance)
            balance = 0
    return balance, shortage


def build_backtest(db: Session, target_start: date, target_end: date, category: str) -> BacktestResult:
    product_ids = _product_ids_for_category(db, category)
    forecast_items = build_statistical_forecast(db, target_start, target_end, category)
    rows: list[BacktestRow] = []
    stock_snapshot_on_start = _has_stock_snapshot_for_category(db, product_ids, target_start - timedelta(days=1))
    stock_snapshot_on_end = _has_stock_snapshot_for_category(db, product_ids, target_end)
    nearest_stock_before_end, nearest_stock_after_end = _nearest_stock_snapshot_dates(db, product_ids, target_end)

    for item in forecast_items:
        actual_purchase = _sum_actual_purchase(db, item.product.id, target_start, target_end)
        actual_sales = _sum_actual_sales(db, item.product.id, target_start, target_end)
        opening_stock, opening_stock_source = _reconstruct_stock_on_date(
            db, item.product.id, target_start - timedelta(days=1)
        )
        actual_leftover, actual_leftover_source = _reconstruct_stock_on_date(db, item.product.id, target_end)
        daily_sales = _quantities_by_date(
            db, Sale, item.product.id, Sale.sale_date, Sale.quantity, target_start, target_end
        )
        daily_purchases = _quantities_by_date(
            db,
            PurchaseOrder,
            item.product.id,
            PurchaseOrder.delivery_date,
            PurchaseOrder.quantity_ordered,
            target_start,
            target_end,
        )
        actual_modeled_leftover, _actual_modeled_shortage = _simulate_leftover_and_shortage(
            opening_stock, daily_purchases, daily_sales, target_start, target_end
        )
        recommended_leftover, recommended_shortage = _simulate_leftover_and_shortage(
            opening_stock + item.statistical_quantity,
            {},
            daily_sales,
            target_start,
            target_end,
        )
        actual_balance_gap = actual_leftover - actual_modeled_leftover
        actual_flow_gap = opening_stock + actual_purchase - actual_sales - actual_leftover
        has_report_data = any(
            value > 0
            for value in (
                actual_purchase,
                actual_sales,
                opening_stock,
                actual_leftover or 0,
                item.statistical_quantity,
                item.historical_sold,
                item.historical_purchased,
            )
        )
        if not has_report_data:
            continue
        purchase_error = item.statistical_quantity - actual_purchase
        sales_error = item.statistical_quantity - actual_sales
        purchase_ape = abs(purchase_error) / actual_purchase * 100 if actual_purchase else None
        sales_ape = abs(sales_error) / actual_sales * 100 if actual_sales else None
        rows.append(
            BacktestRow(
                product_name=item.product.purchase_name,
                recommended=item.statistical_quantity,
                actual_purchase=actual_purchase,
                actual_sales=actual_sales,
                opening_stock=opening_stock,
                opening_stock_source=opening_stock_source,
                actual_leftover=actual_leftover,
                actual_leftover_source=actual_leftover_source,
                recommended_leftover=recommended_leftover,
                recommended_shortage=recommended_shortage,
                actual_modeled_leftover=actual_modeled_leftover,
                actual_balance_gap=actual_balance_gap,
                actual_flow_gap=actual_flow_gap,
                purchase_error=purchase_error,
                sales_error=sales_error,
                purchase_ape=purchase_ape,
                sales_ape=sales_ape,
                historical_sold=item.historical_sold,
                historical_leftover=item.historical_leftover,
                historical_purchased=item.historical_purchased,
                trend_coefficient=item.trend_coefficient,
                explanation=item.explanation,
            )
        )

    purchase_errors = [row.purchase_error for row in rows]
    sales_errors = [row.sales_error for row in rows]
    actual_leftovers = [row.actual_leftover for row in rows]
    balance_gaps = [row.actual_balance_gap for row in rows]
    flow_gaps = [row.actual_flow_gap for row in rows]
    return BacktestResult(
        rows=rows,
        total_recommended=sum(row.recommended for row in rows),
        total_actual_purchase=sum(row.actual_purchase for row in rows),
        total_actual_sales=sum(row.actual_sales for row in rows),
        total_opening_stock=sum(row.opening_stock for row in rows),
        total_actual_leftover=sum(actual_leftovers),
        total_recommended_leftover=sum(row.recommended_leftover for row in rows),
        total_recommended_shortage=sum(row.recommended_shortage for row in rows),
        total_actual_modeled_leftover=sum(row.actual_modeled_leftover for row in rows),
        total_actual_balance_gap=sum(balance_gaps),
        total_actual_flow_gap=sum(flow_gaps),
        purchase_mape=_average([row.purchase_ape for row in rows if row.purchase_ape is not None]),
        sales_mape=_average([row.sales_ape for row in rows if row.sales_ape is not None]),
        purchase_rmse=_rmse(purchase_errors),
        sales_rmse=_rmse(sales_errors),
        stock_snapshot_on_start=stock_snapshot_on_start,
        stock_snapshot_on_end=stock_snapshot_on_end,
        nearest_stock_before_end=nearest_stock_before_end,
        nearest_stock_after_end=nearest_stock_after_end,
    )


def build_analytics_data(db: Session, category: str, start: date, end: date) -> dict:
    product_ids = _product_ids_for_category(db, category)
    if not product_ids:
        return {
            "daily_sales": [],
            "stock_snapshots": [],
            "summary": {
                "sales_total": 0,
                "stock_latest": 0,
                "average_weekly_stock": 0,
                "days_count": (end - start).days + 1,
            },
        }

    sales_by_day = {
        row.sale_date: float(row.quantity or 0)
        for row in db.execute(
            select(Sale.sale_date, func.coalesce(func.sum(Sale.quantity), 0).label("quantity"))
            .where(
                Sale.product_id.in_(product_ids),
                Sale.sale_date >= start,
                Sale.sale_date <= end,
            )
            .group_by(Sale.sale_date)
            .order_by(Sale.sale_date)
        )
    }
    daily_sales = [
        {"date": day.isoformat(), "quantity": sales_by_day.get(day, 0)}
        for day in _date_range(start, end)
    ]

    stock_snapshots = [
        {"date": row.stock_date.isoformat(), "quantity": float(row.quantity or 0)}
        for row in db.execute(
            select(Stock.stock_date, func.coalesce(func.sum(Stock.quantity), 0).label("quantity"))
            .where(
                Stock.product_id.in_(product_ids),
                Stock.stock_date >= start,
                Stock.stock_date <= end,
            )
            .group_by(Stock.stock_date)
            .order_by(Stock.stock_date)
        )
    ]

    latest_stock = stock_snapshots[-1]["quantity"] if stock_snapshots else 0
    store_sales = _sales_by_store(db, product_ids, start, end)
    store_stock = _stock_by_store(db, product_ids, start, end)
    attention = _attention_signals(store_sales, store_stock, (end - start).days + 1)
    result = {
        "daily_sales": daily_sales,
        "stock_snapshots": stock_snapshots,
        "store_stock": store_stock,
        "attention": attention,
        "store_sales_missing_reason": (
            "В загруженных продажах нет привязки к магазинам. "
            "Чтобы построить график и таблицу 'Кто сколько продает', нужен файл продаж с магазинами/точками."
            if not store_sales and sum(row["quantity"] for row in daily_sales) > 0
            else ""
        ),
        "summary": {
            "sales_total": sum(row["quantity"] for row in daily_sales),
            "stock_latest": latest_stock,
            "average_weekly_stock": _average_weekly_stock(db, product_ids, start, end),
            "days_count": (end - start).days + 1,
        },
    }
    if store_sales:
        result["store_sales"] = store_sales
        result["summary"]["stores_count"] = len(store_sales)
    return result


def _sales_by_store(db: Session, product_ids: list[int], start: date, end: date) -> list[dict]:
    if not product_ids:
        return []
    rows = [
        {"store": row.store_name or "Без точки", "quantity": float(row.quantity or 0)}
        for row in db.execute(
            select(Store.name.label("store_name"), func.coalesce(func.sum(Sale.quantity), 0).label("quantity"))
            .join(Store, Store.id == Sale.store_id)
            .where(
                Sale.product_id.in_(product_ids),
                Sale.sale_date >= start,
                Sale.sale_date <= end,
            )
            .group_by(Store.name)
            .order_by(func.coalesce(func.sum(Sale.quantity), 0).desc())
        )
    ]
    return [row for row in rows if row["quantity"] > 0]


def _stock_rows_by_store(db: Session, product_ids: list[int], start: date, end: date) -> list[tuple[date, str, float]]:
    if not product_ids:
        return []
    return [
        (row.stock_date, row.store_name or "Без точки", float(row.quantity or 0))
        for row in db.execute(
            select(
                Stock.stock_date.label("stock_date"),
                Store.name.label("store_name"),
                func.coalesce(func.sum(Stock.quantity), 0).label("quantity"),
            )
            .join(Store, Store.id == Stock.store_id)
            .where(
                Stock.product_id.in_(product_ids),
                Stock.stock_date >= start,
                Stock.stock_date <= end,
            )
            .group_by(Stock.stock_date, Store.name)
            .order_by(Stock.stock_date, Store.name)
        )
    ]


def _stock_by_store(db: Session, product_ids: list[int], start: date, end: date) -> list[dict]:
    rows = _stock_rows_by_store(db, product_ids, start, end)
    if not rows:
        return []

    weekly_by_store: dict[str, dict[tuple[int, int], list[float]]] = {}
    latest_by_store: dict[str, tuple[date, float]] = {}
    for stock_date, store_name, quantity in rows:
        week = stock_date.isocalendar()[:2]
        weekly_by_store.setdefault(store_name, {})
        weekly_by_store[store_name].setdefault(week, []).append(quantity)
        if store_name not in latest_by_store or stock_date >= latest_by_store[store_name][0]:
            latest_by_store[store_name] = (stock_date, quantity)

    result = []
    for store_name, weekly_values in weekly_by_store.items():
        latest_date, latest_quantity = latest_by_store.get(store_name, (None, 0))
        weekly_averages = [_average(values) or 0 for values in weekly_values.values()]
        result.append(
            {
                "store": store_name,
                "latest_quantity": latest_quantity,
                "latest_date": latest_date.isoformat() if latest_date else "",
                "latest_date_display": _format_short_date(latest_date),
                "average_weekly_stock": _average(weekly_averages) or 0,
            }
        )
    return sorted(result, key=lambda row: row["latest_quantity"])


def _average_weekly_stock(db: Session, product_ids: list[int], start: date, end: date) -> float:
    rows = [
        (row.stock_date, float(row.quantity or 0))
        for row in db.execute(
            select(Stock.stock_date, func.coalesce(func.sum(Stock.quantity), 0).label("quantity"))
            .where(
                Stock.product_id.in_(product_ids),
                Stock.stock_date >= start,
                Stock.stock_date <= end,
            )
            .group_by(Stock.stock_date)
        )
    ]
    weekly_totals: dict[tuple[int, int], list[float]] = {}
    for stock_date, quantity in rows:
        week = stock_date.isocalendar()[:2]
        weekly_totals.setdefault(week, []).append(quantity)
    weekly_averages = [_average(values) or 0 for values in weekly_totals.values()]
    return _average(weekly_averages) or 0


def _attention_signals(store_sales: list[dict], store_stock: list[dict], days_count: int) -> list[dict]:
    sales_by_store = {row["store"]: row["quantity"] for row in store_sales}
    signals = []
    for stock_row in store_stock:
        store_name = stock_row["store"]
        sold = sales_by_store.get(store_name, 0)
        avg_daily_sales = sold / max(days_count, 1)
        latest_stock = stock_row["latest_quantity"]
        days_left = latest_stock / avg_daily_sales if avg_daily_sales > 0 else None
        if days_left is not None and days_left <= 3:
            signals.append(
                {
                    "level": "danger" if days_left <= 1.5 else "warning",
                    "store": store_name,
                    "title": "Быстро заканчивается",
                    "text": (
                        f"В точке {store_name} остатка примерно на {days_left:.1f} дн. "
                        f"при текущем темпе продаж. Стоит проверить увеличение поставки."
                    ),
                    "days_left": days_left,
                }
            )
    return sorted(signals, key=lambda row: row["days_left"])


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _format_short_date(value: date | None) -> str:
    return value.strftime("%d.%m.%y") if value else ""


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rmse(errors: list[float]) -> float:
    return sqrt(sum(error * error for error in errors) / len(errors)) if errors else 0
