from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from statistics import median

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Product, PurchaseOrder, RecommendationItem, RecommendationRun, Sale, Stock


HISTORY_YEAR_WEIGHTS = (0.55, 0.3, 0.15)


def _history_weight(index: int) -> float:
    if index < len(HISTORY_YEAR_WEIGHTS):
        return HISTORY_YEAR_WEIGHTS[index]
    return HISTORY_YEAR_WEIGHTS[-1] * (0.5 ** (index - len(HISTORY_YEAR_WEIGHTS) + 1))


def _history_year_offsets(db: Session, product_id: int, anchor_year: int, fallback_years: int = 3) -> list[int]:
    first_sale_year = db.scalar(
        select(func.min(func.strftime("%Y", Sale.sale_date))).where(Sale.product_id == product_id)
    )
    try:
        first_year = int(first_sale_year) if first_sale_year else anchor_year - fallback_years
    except (TypeError, ValueError):
        first_year = anchor_year - fallback_years
    max_offset = max(anchor_year - first_year, fallback_years)
    return list(range(1, max_offset + 1))


def _same_period_years(start: date, end: date, offsets: list[int] | None = None) -> list[tuple[int, date, date]]:
    period_days = (end - start).days + 1
    periods = []
    for offset in offsets or [1, 2, 3]:
        shifted_start = start.replace(year=start.year - offset)
        periods.append((offset, shifted_start, shifted_start + timedelta(days=period_days - 1)))
    return periods


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weighted_sum = sum(value * weight for value, weight in values if value > 0)
    weight_sum = sum(weight for value, weight in values if value > 0)
    return weighted_sum / weight_sum if weight_sum else 0.0


def _history_summary(db: Session, product_id: int, start: date, end: date) -> tuple[float, str]:
    weighted_values = []
    parts = []
    offsets = _history_year_offsets(db, product_id, start.year)
    for index, (offset, period_start, period_end) in enumerate(_same_period_years(start, end, offsets)):
        weight = _history_weight(index)
        sold = _sum_sales(db, product_id, period_start, period_end)
        weighted_values.append((sold, weight))
        parts.append(f"{period_start.year}: {sold:.0f}")
    return _weighted_average(weighted_values), "; ".join(parts)


def _calendar_adjustment_percent(product: Product, events: list[dict], item: "ForecastItem | None" = None) -> float:
    if not events:
        return 0.0
    name = f"{product.purchase_name} {product.sales_category}".lower()
    is_carnation = "гвоздик" in name
    adjustment = 0.0
    for event in events:
        event_name = str(event.get("name", "")).lower()
        if is_carnation and "день победы" in event_name:
            adjustment = max(adjustment, 5.0)
        elif "последний звонок" in event_name:
            adjustment = max(adjustment, 3.0)
        elif "выпускн" in event_name:
            adjustment = max(adjustment, 3.0)
        elif "день семьи" in event_name:
            adjustment = max(adjustment, 2.0)
        elif "день защиты детей" in event_name:
            adjustment = max(adjustment, 1.0)
    if item and adjustment > 0:
        historical_base = max(item.historical_purchased, item.historical_sold, 1)
        leftover_ratio = item.historical_leftover / historical_base
        if leftover_ratio >= 0.2:
            return 0.0
        if leftover_ratio >= 0.1:
            return adjustment / 2
    return adjustment


def _normalize_query(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def _matches_product_query(product: Product, query: str) -> bool:
    normalized = _normalize_query(query)
    if not normalized:
        return True
    if normalized.endswith(" общ"):
        group_name = normalized.removesuffix(" общ").strip()
        return _normalize_query(product.sales_category) == group_name
    haystack = f"{product.purchase_name} {product.sales_category}".lower().replace("ё", "е")
    return all(token in haystack for token in normalized.split())


def _products_for_query(db: Session, query: str) -> list[Product]:
    products_with_data = list(
        db.scalars(
            select(Product).where(
                Product.active.is_(True),
                or_(
                    exists(select(Sale.id).where(Sale.product_id == Product.id)),
                    exists(select(Stock.id).where(Stock.product_id == Product.id)),
                    exists(select(PurchaseOrder.id).where(PurchaseOrder.product_id == Product.id)),
                ),
            )
        )
    )
    normalized = _normalize_query(query)
    if normalized and not normalized.endswith(" общ"):
        exact_products = [
            product
            for product in products_with_data
            if _normalize_query(product.purchase_name) == normalized
        ]
        if exact_products:
            return sorted(exact_products, key=lambda product: product.purchase_name)
    return sorted(
        [product for product in products_with_data if _matches_product_query(product, query)],
        key=lambda product: product.purchase_name,
    )


def _display_category_name(category: str) -> str:
    normalized = " ".join(category.split())
    lowered = normalized.lower().replace("ё", "е")
    if lowered.endswith(" общ"):
        return normalized
    return f"{normalized} общ"


def _calendar_event_names(events: list[dict], product: Product) -> list[str]:
    name = f"{product.purchase_name} {product.sales_category}".lower()
    is_carnation = "гвоздик" in name
    relevant = []
    for event in events:
        event_name = str(event.get("name", ""))
        lowered = event_name.lower()
        if is_carnation and "день победы" in lowered:
            relevant.append(event_name)
        elif any(marker in lowered for marker in ("последний звонок", "выпускн", "день семьи", "день защиты детей")):
            relevant.append(event_name)
    return relevant


def _calendar_adjustment_note(product: Product, events: list[dict], item: "ForecastItem") -> str:
    names = _calendar_event_names(events, product)
    if not names:
        return ""
    historical_base = max(item.historical_purchased, item.historical_sold, 1)
    leftover_ratio = item.historical_leftover / historical_base
    if leftover_ratio >= 0.2:
        return (
            f"События периода учтены ({', '.join(names)}), но календарная прибавка не применена: "
            f"после аналогичного периода прошлого года осталось {item.historical_leftover:.0f} шт. "
            f"при базе {historical_base:.0f} шт., это похоже на прошлогодний излишек."
        )
    return ""


@dataclass(frozen=True)
class ForecastItem:
    product: Product
    statistical_quantity: float
    current_stock: float
    usable_stock: float
    stock_snapshot_date: date | None
    stock_age_days: int | None
    historical_sold: float
    historical_leftover: float
    historical_purchased: float
    historical_purchase_need: float
    incoming_orders: float
    expected_next_receipt_date: date | None
    expected_next_receipt_note: str
    baseline_demand: float
    trend_coefficient: float
    trend_current_sales: float
    trend_previous_sales: float
    trend_current_start: date
    trend_current_end: date
    trend_previous_start: date
    trend_previous_end: date
    short_history_first_sale_date: date | None
    short_history_days: int
    short_history_last_7_sales: float
    short_history_last_30_sales: float
    short_history_weekly_average: float
    short_history_monthly_average: float
    short_history_period_average: float
    safety_stock: float
    explanation: str


@dataclass(frozen=True)
class SalesHistoryStats:
    first_sale_date: date | None
    last_sale_date: date | None
    total_sales: float
    total_days: int
    last_7_sales: float
    last_30_sales: float
    weekly_average: float
    monthly_average: float
    period_average: float
    spike_note: str


def _sum_sales(db: Session, product_id: int, start: date, end: date) -> float:
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


def _latest_stock_snapshot(
    db: Session,
    product_id: int,
    as_of: date,
) -> tuple[date | None, float]:
    latest_date = db.scalar(
        select(func.max(Stock.stock_date)).where(
            Stock.product_id == product_id,
            Stock.stock_date <= as_of,
        )
    )
    if not latest_date:
        return None, 0.0
    quantity = float(
        db.scalar(
            select(func.coalesce(func.sum(Stock.quantity), 0)).where(
                Stock.product_id == product_id,
                Stock.stock_date == latest_date,
            )
        )
        or 0
    )
    return latest_date, quantity


def _usable_stock_snapshot(
    db: Session,
    product_id: int,
    as_of: date,
    shelf_life_days: int,
) -> tuple[date | None, float, int | None]:
    stock_date, quantity = _latest_stock_snapshot(db, product_id, as_of)
    if stock_date is None:
        return None, 0.0, None
    age_days = (as_of - stock_date).days
    if age_days > shelf_life_days:
        return None, 0.0, age_days
    return stock_date, quantity, age_days


def _usable_incoming_orders_sum(
    db: Session,
    product_id: int,
    target_start: date,
    target_end: date,
    shelf_life_days: int,
    order_placed_by: date,
) -> float:
    freshness_start = target_start - timedelta(days=shelf_life_days - 1)
    delivery_start = max(freshness_start, order_placed_by + timedelta(days=1))
    return float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0)).where(
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.delivery_date >= delivery_start,
                PurchaseOrder.delivery_date <= target_end,
                PurchaseOrder.order_date <= order_placed_by,
            )
        )
        or 0
    )


def _fresh_stock_from_recent_receipts(
    db: Session,
    product_id: int,
    current_stock: float,
    as_of: date,
    shelf_life_days: int,
) -> float:
    freshness_start = as_of - timedelta(days=shelf_life_days - 1)
    recent_receipts = float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0)).where(
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.delivery_date >= freshness_start,
                PurchaseOrder.delivery_date <= as_of,
            )
        )
        or 0
    )
    return min(current_stock, recent_receipts)


def _fresh_stock_surviving_until(
    db: Session,
    product_id: int,
    current_stock: float,
    as_of: date,
    fresh_until: date,
    shelf_life_days: int,
) -> float:
    freshness_start = fresh_until - timedelta(days=shelf_life_days - 1)
    receipts_still_fresh = float(
        db.scalar(
            select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0)).where(
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.delivery_date >= freshness_start,
                PurchaseOrder.delivery_date <= as_of,
            )
        )
        or 0
    )
    return min(current_stock, receipts_still_fresh)


def _stock_on_date(db: Session, product_id: int, stock_date: date) -> float:
    return float(
        db.scalar(
            select(func.coalesce(func.sum(Stock.quantity), 0)).where(
                Stock.product_id == product_id,
                Stock.stock_date == stock_date,
            )
        )
        or 0
    )


def _purchase_orders_sum(
    db: Session,
    product_id: int,
    start: date,
    end: date,
    order_placed_by: date | None = None,
) -> float:
    query = select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0)).where(
        PurchaseOrder.product_id == product_id,
        PurchaseOrder.delivery_date >= start,
        PurchaseOrder.delivery_date <= end,
    )
    if order_placed_by:
        query = query.where(PurchaseOrder.order_date <= order_placed_by)
    return float(db.scalar(query) or 0)


def _historical_receipts_for_period_context(
    db: Session,
    product_id: int,
    start: date,
    end: date,
    shelf_life_days: int,
) -> float:
    return _purchase_orders_sum(db, product_id, start - timedelta(days=shelf_life_days), end)


def _recent_daily_sales_rate(db: Session, product_id: int, start: date, end: date) -> float:
    days = max((end - start).days + 1, 1)
    return _sum_sales(db, product_id, start, end) / days


def _sales_history_stats(db: Session, product_id: int, as_of: date, period_days: int) -> SalesHistoryStats:
    first_sale_date = db.scalar(
        select(func.min(Sale.sale_date)).where(
            Sale.product_id == product_id,
            Sale.sale_date <= as_of,
        )
    )
    last_sale_date = db.scalar(
        select(func.max(Sale.sale_date)).where(
            Sale.product_id == product_id,
            Sale.sale_date <= as_of,
        )
    )
    if not first_sale_date:
        return SalesHistoryStats(
            first_sale_date=None,
            last_sale_date=None,
            total_sales=0.0,
            total_days=0,
            last_7_sales=0.0,
            last_30_sales=0.0,
            weekly_average=0.0,
            monthly_average=0.0,
            period_average=0.0,
            spike_note="",
        )

    total_days = max((as_of - first_sale_date).days + 1, 1)
    total_sales = _sum_sales(db, product_id, first_sale_date, as_of)
    last_7_start = max(first_sale_date, as_of - timedelta(days=6))
    last_30_start = max(first_sale_date, as_of - timedelta(days=29))
    last_7_days = max((as_of - last_7_start).days + 1, 1)
    last_30_days = max((as_of - last_30_start).days + 1, 1)
    last_7_sales = _sum_sales(db, product_id, last_7_start, as_of)
    last_30_sales = _sum_sales(db, product_id, last_30_start, as_of)
    weekly_average = last_7_sales / last_7_days * period_days
    monthly_average = last_30_sales / last_30_days * period_days
    period_average = total_sales / total_days * period_days

    daily_rows = list(
        db.execute(
            select(Sale.sale_date, func.coalesce(func.sum(Sale.quantity), 0).label("quantity"))
            .where(
                Sale.product_id == product_id,
                Sale.sale_date >= first_sale_date,
                Sale.sale_date <= as_of,
            )
            .group_by(Sale.sale_date)
            .order_by(Sale.sale_date)
        )
    )
    spike_note = ""
    positive_days = [float(row.quantity or 0) for row in daily_rows if float(row.quantity or 0) > 0]
    if len(positive_days) >= 3:
        average_positive_day = sum(positive_days) / len(positive_days)
        spike_rows = [
            row
            for row in daily_rows
            if float(row.quantity or 0) >= max(average_positive_day * 3, 20)
        ]
        if spike_rows:
            spike = max(spike_rows, key=lambda row: float(row.quantity or 0))
            events = []
            try:
                from app.services.event_calendar import demand_events_for_period

                events = [
                    event.name
                    for event in demand_events_for_period(spike.sale_date, spike.sale_date)
                    if event.start <= spike.sale_date <= event.end
                ]
            except Exception:
                events = []
            if events:
                spike_note = (
                    f"замечен всплеск {float(spike.quantity or 0):.0f} шт. "
                    f"{spike.sale_date.isoformat()}, рядом событие: {', '.join(events[:3])}"
                )
            else:
                spike_note = (
                    f"замечен всплеск {float(spike.quantity or 0):.0f} шт. "
                    f"{spike.sale_date.isoformat()}; похоже на разовую крупную продажу, "
                    f"нужно проверить, не был ли это опт"
                )

    return SalesHistoryStats(
        first_sale_date=first_sale_date,
        last_sale_date=last_sale_date,
        total_sales=total_sales,
        total_days=total_days,
        last_7_sales=last_7_sales,
        last_30_sales=last_30_sales,
        weekly_average=weekly_average,
        monthly_average=monthly_average,
        period_average=period_average,
        spike_note=spike_note,
    )


def _short_history_baseline(stats: SalesHistoryStats) -> float:
    candidates = [stats.period_average]
    if stats.total_days >= 7:
        candidates.append(stats.weekly_average)
    if stats.total_days >= 30:
        candidates.append(stats.monthly_average)
    positive = [value for value in candidates if value > 0]
    if not positive:
        return 0.0
    return max(positive)


def _self_checked_statistical_quantity(
    *,
    raw_quantity: float,
    baseline: float,
    fresh_available_for_target: float,
    incoming: float,
    safety: float,
    stock_date: date | None,
    target_start: date,
    shelf_life_days: int,
) -> tuple[float, str]:
    notes = []
    checked_quantity = raw_quantity
    available = fresh_available_for_target + incoming
    expected_minimum = max(baseline + safety - available, 0)

    if baseline > 0 and checked_quantity <= 0 and expected_minimum > 0:
        checked_quantity = expected_minimum
        notes.append(
            "расчет был исправлен: при ненулевом спросе нельзя ставить 0, "
            "если свежего остатка и уже заказанных поставок не хватает на период"
        )

    if stock_date and fresh_available_for_target > 0 and (target_start - stock_date).days >= shelf_life_days:
        notes.append(
            "проверить свежесть остатка: часть остатка попала к вычету, хотя дата остатка близка к пределу хранения"
        )

    if baseline == 0 and checked_quantity > 0:
        notes.append("проверить расчет: заказ появился при нулевом ожидаемом спросе")

    return checked_quantity, "; ".join(notes)


def _month_over_month_trend(
    db: Session,
    product_id: int,
    as_of: date,
    first_sale_date: date | None,
) -> tuple[float, bool, float, float, date, date, date, date]:
    current_end = as_of
    current_start = max(first_sale_date or as_of, as_of - timedelta(days=29))
    current_days = max((current_end - current_start).days + 1, 1)
    current_sales = _sum_sales(db, product_id, current_start, current_end)

    previous_values = []
    previous_sales = 0.0
    previous_parts_total = 0.0
    previous_start = current_start - timedelta(days=30)
    previous_end = current_start - timedelta(days=1)
    cursor_end = previous_end
    index = 0
    while first_sale_date and cursor_end >= first_sale_date:
        cursor_start = max(first_sale_date, cursor_end - timedelta(days=29))
        days = max((cursor_end - cursor_start).days + 1, 1)
        if days >= 14:
            sales = _sum_sales(db, product_id, cursor_start, cursor_end)
            weight = 1 / (index + 1)
            previous_values.append((sales / days, weight))
            previous_parts_total += sales
            previous_start = cursor_start
        cursor_end = cursor_start - timedelta(days=1)
        index += 1

    if not previous_values:
        total_days = max((as_of - first_sale_date).days + 1, 1) if first_sale_date else 0
        if first_sale_date and total_days >= 14:
            split_days = total_days // 2
            previous_start = first_sale_date
            previous_end = first_sale_date + timedelta(days=split_days - 1)
            current_start = previous_end + timedelta(days=1)
            current_end = as_of
            previous_days = max((previous_end - previous_start).days + 1, 1)
            current_days = max((current_end - current_start).days + 1, 1)
            previous_sales = _sum_sales(db, product_id, previous_start, previous_end)
            current_sales = _sum_sales(db, product_id, current_start, current_end)
            previous_daily = previous_sales / previous_days
            current_daily = current_sales / current_days
            active_days = db.scalar(
                select(func.count()).select_from(
                    select(Sale.sale_date)
                    .where(
                        Sale.product_id == product_id,
                        Sale.sale_date >= first_sale_date,
                        Sale.sale_date <= as_of,
                    )
                    .group_by(Sale.sale_date)
                    .subquery()
                )
            ) or 0
            if previous_daily > 0 and active_days >= 3:
                trend = current_daily / previous_daily
                return (
                    max(0.4, min(trend, 2.5)),
                    True,
                    current_sales,
                    previous_sales,
                    current_start,
                    current_end,
                    previous_start,
                    previous_end,
                )
        return 1.0, False, current_sales, previous_sales, current_start, current_end, previous_start, previous_end
    current_daily = current_sales / current_days
    previous_daily = sum(value * weight for value, weight in previous_values if value > 0) / sum(
        weight for value, weight in previous_values if value > 0
    ) if any(value > 0 for value, _weight in previous_values) else 0.0
    previous_sales = previous_daily * current_days
    if previous_daily <= 0:
        return 1.0, False, current_sales, previous_parts_total, current_start, current_end, previous_start, previous_end
    trend = current_daily / previous_daily if previous_daily > 0 else 1.0
    return max(0.4, min(trend, 2.5)), True, current_sales, previous_sales, current_start, current_end, previous_start, previous_end


def _validated_ai_quantity(item: ForecastItem, value, *, allow_increase: bool = False) -> tuple[float, str]:
    fallback = float(item.statistical_quantity)
    if value is None:
        return fallback, ""
    try:
        quantity = float(value)
    except (TypeError, ValueError):
        return fallback, "Самопроверка: AI вернул нечисловую рекомендацию, оставлен статистический расчет."

    if fallback > 0 and quantity <= 0:
        return fallback, "Самопроверка: AI вернул 0 при ненулевом статистическом расчете, оставлен статистический расчет."
    if fallback > 0 and quantity < fallback * 0.7:
        return fallback, "Самопроверка: AI слишком сильно снизил рекомендацию без расчетного основания, оставлен статистический расчет."
    if fallback > 0 and not allow_increase and quantity > fallback * 1.3:
        return fallback, "Самопроверка: AI слишком сильно увеличил рекомендацию без календарного режима, оставлен статистический расчет."
    return max(quantity, 0.0), ""


def _expected_next_receipt_date(
    db: Session,
    product_id: int,
    as_of: date,
    lookback_start: date,
) -> tuple[date | None, str]:
    receipt_dates = list(
        db.scalars(
            select(PurchaseOrder.delivery_date)
            .where(
                PurchaseOrder.product_id == product_id,
                PurchaseOrder.delivery_date >= lookback_start,
                PurchaseOrder.delivery_date <= as_of,
                PurchaseOrder.quantity_ordered > 0,
            )
            .group_by(PurchaseOrder.delivery_date)
            .order_by(PurchaseOrder.delivery_date)
        )
    )
    if len(receipt_dates) < 2:
        return None, "нет данных"

    intervals = [
        (current - previous).days
        for previous, current in zip(receipt_dates, receipt_dates[1:], strict=False)
        if (current - previous).days > 0
    ]
    if not intervals:
        return None, "нет данных"

    typical_interval_days = max(1, int(round(median(intervals))))
    expected = receipt_dates[-1] + timedelta(days=typical_interval_days)
    while expected <= as_of:
        expected += timedelta(days=typical_interval_days)
    return expected, f"прогноз по ритму фактических приходов, типичный интервал {typical_interval_days} дн."


def _shelf_life_days_for_product(product: Product) -> int:
    settings = get_settings()
    flower_type = (product.flower_type or "").lower()
    name = f"{product.purchase_name} {product.sales_category}".lower()
    if flower_type == "carnation" or "гвоздик" in name:
        return settings.carnation_shelf_life_days
    if flower_type == "chrysanthemum" or "хризантем" in name:
        return settings.chrysanthemum_shelf_life_days
    if flower_type == "rose" or "роза" in name:
        return settings.rose_shelf_life_days
    return settings.stock_shelf_life_days


def build_statistical_forecast(db: Session, target_start: date, target_end: date, category: str) -> list[ForecastItem]:
    settings = get_settings()
    products = _products_for_query(db, category)
    if not products:
        products = list(
            db.scalars(
                select(Product).where(
                    Product.sales_category == category,
                    Product.active.is_(True),
                    or_(
                        exists(select(Sale.id).where(Sale.product_id == Product.id)),
                        exists(select(Stock.id).where(Stock.product_id == Product.id)),
                        exists(select(PurchaseOrder.id).where(PurchaseOrder.product_id == Product.id)),
                    ),
                )
            )
        )
    items: list[ForecastItem] = []
    period_days = (target_end - target_start).days + 1
    same_start_last_year = target_start.replace(year=target_start.year - 1)
    same_end_last_year = same_start_last_year + timedelta(days=period_days - 1)
    calculation_as_of = min(target_start - timedelta(days=1), date.today())

    lookback_days = settings.trend_lookback_weeks * 7
    current_lookback_end = calculation_as_of
    current_lookback_start = current_lookback_end - timedelta(days=lookback_days - 1)
    previous_lookback_start = current_lookback_start.replace(year=current_lookback_start.year - 1)
    previous_lookback_end = current_lookback_end.replace(year=current_lookback_end.year - 1)

    for product in products:
        multi_year_sales, multi_year_parts = _history_summary(db, product.id, target_start, target_end)
        same_period_sales = _sum_sales(db, product.id, same_start_last_year, same_end_last_year)
        historical_leftover = _stock_on_date(db, product.id, same_end_last_year)
        historical_purchase_need = same_period_sales
        trend_current_start = current_lookback_start
        trend_current_end = current_lookback_end
        trend_previous_start = previous_lookback_start
        trend_previous_end = previous_lookback_end
        current_recent_sales = _sum_sales(db, product.id, current_lookback_start, current_lookback_end)
        previous_recent_values = []
        previous_recent_parts = []
        history_offsets = _history_year_offsets(db, product.id, current_lookback_start.year)
        for index, (_offset, period_start, period_end) in enumerate(
            _same_period_years(current_lookback_start, current_lookback_end, history_offsets)
        ):
            weight = _history_weight(index)
            sold = _sum_sales(db, product.id, period_start, period_end)
            previous_recent_values.append((sold, weight))
            previous_recent_parts.append(f"{period_start.year}: {sold:.0f}")
        previous_recent_sales = _weighted_average(previous_recent_values)
        has_trend_history = previous_recent_sales > 0
        sales_stats = _sales_history_stats(db, product.id, calculation_as_of, period_days)
        trend = current_recent_sales / previous_recent_sales if has_trend_history else 1.0
        trend = max(0.4, min(trend, 2.5))
        trend_basis = "year"
        if multi_year_sales > 0:
            baseline = multi_year_sales * trend
            baseline_source = "взвешенная история аналогичных периодов за последние годы с учетом тренда"
        elif sales_stats.total_sales > 0:
            (
                monthly_trend,
                has_monthly_trend,
                monthly_current_sales,
                monthly_previous_sales,
                monthly_current_start,
                monthly_current_end,
                monthly_previous_start,
                monthly_previous_end,
            ) = _month_over_month_trend(db, product.id, calculation_as_of, sales_stats.first_sale_date)
            trend = monthly_trend
            has_trend_history = has_monthly_trend
            trend_basis = "month"
            current_recent_sales = monthly_current_sales
            previous_recent_sales = monthly_previous_sales
            trend_current_start = monthly_current_start
            trend_current_end = monthly_current_end
            trend_previous_start = monthly_previous_start
            trend_previous_end = monthly_previous_end
            baseline = _short_history_baseline(sales_stats) * trend
            if sales_stats.total_days < 60:
                baseline_source = (
                    f"мало данных для анализа: нет истории за прошлые годы, "
                    f"используем всю доступную историю продаж с {sales_stats.first_sale_date.isoformat()} "
                    f"по {calculation_as_of.isoformat()} ({sales_stats.total_days} дн.); "
                    f"средний спрос на расчетный период: за неделю {sales_stats.weekly_average:.0f}, "
                    f"за 30 дней {sales_stats.monthly_average:.0f}, "
                    f"за весь период {sales_stats.period_average:.0f}; "
                    f"месячный тренд: {trend:.2f}"
                )
            else:
                baseline_source = (
                    f"нет истории за прошлые годы, используем всю доступную историю продаж; "
                    f"средний спрос на расчетный период: за неделю {sales_stats.weekly_average:.0f}, "
                    f"за 30 дней {sales_stats.monthly_average:.0f}, "
                    f"за весь период {sales_stats.period_average:.0f}; "
                    f"месячный тренд: {trend:.2f}"
                )
            if sales_stats.spike_note:
                baseline_source += f"; {sales_stats.spike_note}"
        else:
            baseline = 0
            baseline_source = "нет истории за прошлые годы и нет текущих продаж для расчета спроса"
        shelf_life_days = _shelf_life_days_for_product(product)
        historical_purchased = _historical_receipts_for_period_context(
            db,
            product.id,
            same_start_last_year,
            same_end_last_year,
            shelf_life_days,
        )
        stock_date, current_stock, stock_age_days = _usable_stock_snapshot(
            db,
            product.id,
            calculation_as_of,
            shelf_life_days,
        )
        fresh_stock_now = _fresh_stock_from_recent_receipts(
            db,
            product.id,
            current_stock,
            calculation_as_of,
            shelf_life_days,
        )
        recent_sales_rate = _recent_daily_sales_rate(db, product.id, current_lookback_start, current_lookback_end)
        target_daily_demand = baseline / max(period_days, 1)
        reserve_daily_demand = max(recent_sales_rate, target_daily_demand)
        next_receipt, next_receipt_source = _expected_next_receipt_date(
            db,
            product.id,
            calculation_as_of,
            current_lookback_start,
        )
        reserve_until = target_start
        bridge_days = max((reserve_until - calculation_as_of).days - 1, 0)
        bridge_need = reserve_daily_demand * bridge_days
        fresh_stock_at_next_receipt = _fresh_stock_surviving_until(
            db,
            product.id,
            current_stock,
            calculation_as_of,
            target_start,
            shelf_life_days,
        )
        fresh_incoming_start = max(calculation_as_of + timedelta(days=1), target_start - timedelta(days=shelf_life_days - 1))
        incoming_before_target = (
            _purchase_orders_sum(
                db,
                product.id,
                fresh_incoming_start,
                target_start - timedelta(days=1),
                calculation_as_of,
            )
            if bridge_days > 0 and fresh_incoming_start <= target_start - timedelta(days=1)
            else 0.0
        )
        fresh_available_for_target = max(fresh_stock_at_next_receipt + incoming_before_target - bridge_need, 0)
        incoming = _usable_incoming_orders_sum(
            db,
            product.id,
            target_start,
            target_end,
            shelf_life_days,
            calculation_as_of,
        )
        safety = baseline * (settings.safety_stock_percent / 100)
        statistical_quantity = max(baseline - fresh_available_for_target - incoming + safety, 0)
        statistical_quantity, self_check_note = _self_checked_statistical_quantity(
            raw_quantity=statistical_quantity,
            baseline=baseline,
            fresh_available_for_target=fresh_available_for_target,
            incoming=incoming,
            safety=safety,
            stock_date=stock_date,
            target_start=target_start,
            shelf_life_days=shelf_life_days,
        )
        explanation = (
            f"Аналогичный период прошлого года: {same_period_sales:.0f}; "
            f"взвешенная история по годам: {multi_year_parts}; "
            f"закуплено в аналогичный период: {historical_purchased:.0f}; "
            f"остаток после аналогичного периода: {historical_leftover:.0f} "
            f"(показывает прошлогодний излишек, но не вычитается из проданного спроса); "
            f"историческая потребность по продажам: {multi_year_sales:.0f}; "
            f"база рекомендации: {baseline_source}; "
            f"ожидаемый спрос: {baseline:.0f}; "
            f"коэффициент тренда: {trend:.2f} "
            + (
                f"({current_recent_sales:.0f} за {current_lookback_start.isoformat()}-{current_lookback_end.isoformat()} / "
                f"{previous_recent_sales:.0f} средневзвешенно по прошлым годам: {', '.join(previous_recent_parts)}); "
                if has_trend_history and trend_basis == "year"
                else (
                    f"месячный тренд {trend:.2f}: {current_recent_sales:.0f} шт. "
                    f"за {trend_current_start.isoformat()}-{trend_current_end.isoformat()} / "
                    f"{previous_recent_sales:.0f} шт. за {trend_previous_start.isoformat()}-{trend_previous_end.isoformat()}; "
                    if has_trend_history
                    else "тренд не рассчитывается, потому что пока недостаточно предыдущего месяца продаж; "
                )
            )
            +
            f"остаток на дату расчета: {current_stock:.0f}; "
            f"свежий остаток на дату расчета: {fresh_stock_now:.0f}; "
            f"из него доживет до начала расчетного периода: {fresh_stock_at_next_receipt:.0f}; "
            f"прогноз продаж до начала расчетного периода ({reserve_until.isoformat()}): {bridge_need:.0f} "
            f"({reserve_daily_demand:.1f} шт./день, максимум из текущего темпа и спроса расчетного периода); "
            f"к вычету из заказа остается: {fresh_available_for_target:.0f}; "
            f"следующий приход товара: {next_receipt.isoformat() if next_receipt else 'нет данных'} "
            f"({next_receipt_source}); "
            f"срок хранения для позиции: {shelf_life_days} дн.; "
            f"уже заказано к свежей поставке по данным поступлений: {incoming:.0f}; страховой запас: {safety:.0f}."
        )
        if self_check_note:
            explanation = f"{explanation} Самопроверка системы: {self_check_note}."
        items.append(
            ForecastItem(
                product=product,
                statistical_quantity=float(ceil(statistical_quantity)),
                current_stock=current_stock,
                usable_stock=fresh_available_for_target,
                stock_snapshot_date=stock_date,
                stock_age_days=stock_age_days,
                historical_sold=same_period_sales,
                historical_leftover=historical_leftover,
                historical_purchased=historical_purchased,
                historical_purchase_need=historical_purchase_need,
                incoming_orders=incoming,
                expected_next_receipt_date=next_receipt,
                expected_next_receipt_note=next_receipt_source,
                baseline_demand=baseline,
                trend_coefficient=trend,
                trend_current_sales=current_recent_sales,
                trend_previous_sales=previous_recent_sales,
                trend_current_start=trend_current_start,
                trend_current_end=trend_current_end,
                trend_previous_start=trend_previous_start,
                trend_previous_end=trend_previous_end,
                short_history_first_sale_date=sales_stats.first_sale_date,
                short_history_days=sales_stats.total_days,
                short_history_last_7_sales=sales_stats.last_7_sales,
                short_history_last_30_sales=sales_stats.last_30_sales,
                short_history_weekly_average=sales_stats.weekly_average,
                short_history_monthly_average=sales_stats.monthly_average,
                short_history_period_average=sales_stats.period_average,
                safety_stock=safety,
                explanation=explanation,
            )
        )
    return items


def save_recommendation_run(
    db: Session,
    *,
    target_start: date,
    target_end: date,
    category: str,
    items: list[ForecastItem],
    notes: str = "",
    primary_ai: dict | None = None,
    event_ai: dict | None = None,
) -> RecommendationRun:
    run = RecommendationRun(
        target_start_date=target_start,
        target_end_date=target_end,
        category=category,
        notes=notes,
    )
    db.add(run)
    db.flush()
    primary_by_name = {
        row.get("purchase_name"): row
        for row in (primary_ai or {}).get("primary_items", [])
        if isinstance(row, dict)
    }
    event_by_name = {
        row.get("purchase_name"): row
        for row in (event_ai or {}).get("event_items", [])
        if isinstance(row, dict)
    }
    calendar_events = (event_ai or {}).get("events", [])
    for item in items:
        primary = primary_by_name.get(item.product.purchase_name, {})
        event = event_by_name.get(item.product.purchase_name, {})
        primary_quantity, primary_check_note = _validated_ai_quantity(
            item,
            primary.get("recommended_quantity"),
            allow_increase=False,
        )
        event_quantity_raw = event.get("recommended_quantity")
        event_quantity, event_check_note = _validated_ai_quantity(
            item,
            event_quantity_raw,
            allow_increase=True,
        ) if event_quantity_raw is not None else (None, "")
        calendar_adjustment = _calendar_adjustment_percent(item.product, calendar_events, item)
        forced_calendar_adjustment = False
        if calendar_adjustment > 0 and primary_quantity > 0:
            calendar_quantity = ceil(primary_quantity * (1 + calendar_adjustment / 100))
            if event_quantity is None or event_quantity < calendar_quantity:
                event_quantity = calendar_quantity
                forced_calendar_adjustment = True
        final_quantity = float(event_quantity if event_quantity is not None else primary_quantity)
        explanation = (
            event.get("recommendation_text")
            or primary.get("recommendation_text")
            or item.explanation
        )
        if forced_calendar_adjustment:
            names = ", ".join(_calendar_event_names(calendar_events, item.product))
            explanation = (
                f"{item.explanation} Календарная поправка: +{calendar_adjustment:.0f}% "
                f"с учетом событий периода ({names})."
            )
        elif calendar_adjustment == 0:
            calendar_note = _calendar_adjustment_note(item.product, calendar_events, item)
            if calendar_note:
                explanation = f"{item.explanation} {calendar_note}"
        elif calendar_adjustment > 0 and not event.get("recommendation_text"):
            names = ", ".join(_calendar_event_names(calendar_events, item.product))
            explanation = (
                f"{explanation} Календарная поправка: +{calendar_adjustment:.0f}% "
                f"с учетом событий периода ({names})."
            )
        ai_check_notes = " ".join(note for note in (primary_check_note, event_check_note) if note)
        if ai_check_notes:
            explanation = f"{explanation} {ai_check_notes}"
        db.add(
            RecommendationItem(
                run_id=run.id,
                product_id=item.product.id,
                statistical_quantity=item.statistical_quantity,
                ai_quantity=float(event_quantity) if event_quantity is not None else primary_quantity,
                final_quantity=final_quantity,
                current_stock=item.current_stock,
                usable_stock=item.usable_stock,
                stock_snapshot_date=item.stock_snapshot_date,
                stock_age_days=item.stock_age_days,
                historical_sold=item.historical_sold,
                historical_leftover=item.historical_leftover,
                historical_purchased=item.historical_purchased,
                historical_purchase_need=item.historical_purchase_need,
                incoming_orders=item.incoming_orders,
                expected_next_receipt_date=item.expected_next_receipt_date,
                expected_next_receipt_note=item.expected_next_receipt_note,
                baseline_demand=item.baseline_demand,
                trend_coefficient=item.trend_coefficient,
                trend_current_sales=item.trend_current_sales,
                trend_previous_sales=item.trend_previous_sales,
                trend_current_start=item.trend_current_start,
                trend_current_end=item.trend_current_end,
                trend_previous_start=item.trend_previous_start,
                trend_previous_end=item.trend_previous_end,
                short_history_first_sale_date=item.short_history_first_sale_date,
                short_history_days=item.short_history_days,
                short_history_last_7_sales=item.short_history_last_7_sales,
                short_history_last_30_sales=item.short_history_last_30_sales,
                short_history_weekly_average=item.short_history_weekly_average,
                short_history_monthly_average=item.short_history_monthly_average,
                short_history_period_average=item.short_history_period_average,
                safety_stock=item.safety_stock,
                explanation=explanation,
            )
        )
    db.commit()
    db.refresh(run)
    return run


def evaluate_against_actual_purchases(db: Session, run: RecommendationRun) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for item in run.items:
        actual = _purchase_orders_sum(db, item.product_id, run.target_start_date, run.target_end_date)
        if item.final_quantity <= 0 and actual <= 0:
            continue
        error = item.final_quantity - actual
        ape = abs(error) / actual * 100 if actual > 0 else None
        rows.append(
            {
                "product": item.product.purchase_name,
                "recommended": item.final_quantity,
                "actual": actual,
                "error": error,
                "absolute_percentage_error": ape,
            }
        )
    return rows
