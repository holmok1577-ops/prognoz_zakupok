from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from statistics import median

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Product, PurchaseOrder, RecommendationItem, RecommendationRun, Sale, Stock


HISTORY_YEAR_WEIGHTS = (0.55, 0.3, 0.15)


def _same_period_years(start: date, end: date, years_back: int = 3) -> list[tuple[int, date, date]]:
    period_days = (end - start).days + 1
    periods = []
    for offset in range(1, years_back + 1):
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
    for index, (offset, period_start, period_end) in enumerate(_same_period_years(start, end)):
        weight = HISTORY_YEAR_WEIGHTS[index] if index < len(HISTORY_YEAR_WEIGHTS) else 0.0
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
    safety_stock: float
    explanation: str


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
        current_recent_sales = _sum_sales(db, product.id, current_lookback_start, current_lookback_end)
        previous_recent_values = []
        previous_recent_parts = []
        for index, (_offset, period_start, period_end) in enumerate(
            _same_period_years(current_lookback_start, current_lookback_end)
        ):
            weight = HISTORY_YEAR_WEIGHTS[index] if index < len(HISTORY_YEAR_WEIGHTS) else 0.0
            sold = _sum_sales(db, product.id, period_start, period_end)
            previous_recent_values.append((sold, weight))
            previous_recent_parts.append(f"{period_start.year}: {sold:.0f}")
        previous_recent_sales = _weighted_average(previous_recent_values)
        trend = current_recent_sales / previous_recent_sales if previous_recent_sales > 0 else 1.0
        trend = max(0.4, min(trend, 2.5))
        if multi_year_sales > 0:
            baseline = multi_year_sales * trend
            baseline_source = "взвешенная история аналогичных периодов за последние годы с учетом тренда"
        elif current_recent_sales > 0:
            lookback_days = max((current_lookback_end - current_lookback_start).days + 1, 1)
            baseline = current_recent_sales / lookback_days * period_days
            baseline_source = "средние недавние продажи, потому что аналогичный период прошлого года пустой"
        else:
            baseline = 0
            baseline_source = "данных для спроса нет"
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
        reserve_until = next_receipt if next_receipt else target_start
        reserve_until = max(reserve_until, target_start)
        bridge_days = max((reserve_until - calculation_as_of).days - 1, 0)
        bridge_need = reserve_daily_demand * bridge_days
        fresh_stock_at_next_receipt = _fresh_stock_surviving_until(
            db,
            product.id,
            current_stock,
            calculation_as_of,
            reserve_until,
            shelf_life_days,
        )
        incoming_before_target = _purchase_orders_sum(
            db,
            product.id,
            calculation_as_of + timedelta(days=1),
            target_start - timedelta(days=1),
            calculation_as_of,
        ) if bridge_days > 0 else 0.0
        fresh_available_for_target = max(fresh_stock_at_next_receipt + incoming_before_target - bridge_need, 0)
        incoming = _usable_incoming_orders_sum(
            db,
            product.id,
            target_start,
            target_end,
            shelf_life_days,
            calculation_as_of,
        )
        incoming = max(incoming - incoming_before_target, 0)
        safety = baseline * (settings.safety_stock_percent / 100)
        statistical_quantity = max(baseline - fresh_available_for_target - incoming + safety, 0)
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
            f"({current_recent_sales:.0f} за {current_lookback_start.isoformat()}-{current_lookback_end.isoformat()} / "
            f"{previous_recent_sales:.0f} средневзвешенно по прошлым годам: {', '.join(previous_recent_parts)}); "
            f"остаток на дату расчета: {current_stock:.0f}; "
            f"свежий остаток на дату расчета: {fresh_stock_now:.0f}; "
            f"из него доживет до ожидаемого следующего прихода: {fresh_stock_at_next_receipt:.0f}; "
            f"прогноз продаж до ожидаемого следующего прихода ({reserve_until.isoformat()}): {bridge_need:.0f} "
            f"({reserve_daily_demand:.1f} шт./день, максимум из текущего темпа и спроса расчетного периода); "
            f"к вычету из заказа остается: {fresh_available_for_target:.0f}; "
            f"следующий приход товара: {next_receipt.isoformat() if next_receipt else 'нет данных'} "
            f"({next_receipt_source}); "
            f"срок хранения для позиции: {shelf_life_days} дн.; "
            f"уже заказано к свежей поставке по данным поступлений: {incoming:.0f}; страховой запас: {safety:.0f}."
        )
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
                trend_current_start=current_lookback_start,
                trend_current_end=current_lookback_end,
                trend_previous_start=previous_lookback_start,
                trend_previous_end=previous_lookback_end,
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
        primary_quantity = float(primary.get("recommended_quantity") or item.statistical_quantity)
        event_quantity = event.get("recommended_quantity")
        calendar_adjustment = _calendar_adjustment_percent(item.product, calendar_events, item)
        forced_calendar_adjustment = False
        if calendar_adjustment > 0 and primary_quantity > 0:
            calendar_quantity = ceil(primary_quantity * (1 + calendar_adjustment / 100))
            if event_quantity is None or float(event_quantity) < calendar_quantity:
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
