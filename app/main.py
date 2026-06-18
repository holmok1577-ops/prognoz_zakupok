import json
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db, init_db
from app.models import Product, PurchaseOrder, RecommendationItem, RecommendationRun, Sale, Stock, Store
from app.services.ai import build_ai_recommendation, build_primary_ai_recommendation
from app.services.analytics import build_analytics_data, build_backtest
from app.services.forecast import (
    build_statistical_forecast,
    evaluate_against_actual_purchases,
    _display_category_name,
    _products_for_query,
    save_recommendation_run,
)
from app.services.importer import (
    ImportValidationError,
    import_products_csv,
    import_purchases_csv,
    import_sales_csv,
    import_stocks_csv,
)
from app.services.onec_sync import sync_from_onec
from app.services.onec_xls_importer import (
    import_onec_movements_xls_as_purchases,
    import_onec_products_xls,
    import_onec_sales_xls,
    import_onec_stocks_xls,
)


settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.app_debug)
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _default_forecast_period(today: date | None = None) -> tuple[date, date]:
    start = today or date.today()
    end = start + timedelta(days=settings.forecast_period_days - 1)
    return start, end


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    has_product_data = or_(
        exists(select(Sale.id).where(Sale.product_id == Product.id)),
        exists(select(Stock.id).where(Stock.product_id == Product.id)),
        exists(select(PurchaseOrder.id).where(PurchaseOrder.product_id == Product.id)),
    )
    products_count = (
        db.scalar(
            select(func.count(Product.id)).where(
                Product.active.is_(True),
                has_product_data,
            )
        )
        or 0
    )
    sales_count = db.scalar(select(func.count(Sale.id))) or 0
    stocks_count = db.scalar(select(func.count(Stock.id))) or 0
    purchases_count = db.scalar(select(func.count(PurchaseOrder.id))) or 0
    sales_quantity = db.scalar(select(func.coalesce(func.sum(Sale.quantity), 0))) or 0
    today = date.today()
    latest_stock_date = db.scalar(select(func.max(Stock.stock_date)).where(Stock.stock_date <= today))
    if latest_stock_date is None:
        latest_stock_date = db.scalar(select(func.max(Stock.stock_date)))
    stocks_snapshot_count = 0
    stocks_quantity = 0
    if latest_stock_date:
        stocks_snapshot_count = (
            db.scalar(select(func.count(Stock.id)).where(Stock.stock_date == latest_stock_date)) or 0
        )
        stocks_quantity = (
            db.scalar(select(func.coalesce(func.sum(Stock.quantity), 0)).where(Stock.stock_date == latest_stock_date))
            or 0
        )
    purchases_quantity = db.scalar(select(func.coalesce(func.sum(PurchaseOrder.quantity_ordered), 0))) or 0
    latest_run = db.scalar(select(RecommendationRun).order_by(desc(RecommendationRun.created_at)).limit(1))
    categories = set(db.scalars(select(Product.sales_category).where(Product.active.is_(True))))
    product_options = sorted(
        {f"{category} общ" for category in categories if category}
        | set(
            db.scalars(
                select(Product.purchase_name).where(
                    Product.active.is_(True),
                    has_product_data,
                )
            )
        )
    )
    default_start, default_end = _default_forecast_period()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "settings": settings,
            "products_count": products_count,
            "sales_count": sales_count,
            "stocks_count": stocks_count,
            "stocks_snapshot_count": stocks_snapshot_count,
            "latest_stock_date": latest_stock_date,
            "purchases_count": purchases_count,
            "sales_quantity": sales_quantity,
            "stocks_quantity": stocks_quantity,
            "purchases_quantity": purchases_quantity,
            "latest_run": latest_run,
            "product_options": product_options,
            "default_start": default_start,
            "default_end": default_end,
            "sync_status": request.query_params.get("sync_status", ""),
            "sync_message": request.query_params.get("sync_message", ""),
            "import_status": request.query_params.get("import_status", ""),
            "import_message": request.query_params.get("import_message", ""),
        },
    )


@app.post("/import/{kind}")
async def import_csv(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    handlers = {
        "products": import_products_csv,
        "sales": import_sales_csv,
        "stocks": import_stocks_csv,
        "purchases": import_purchases_csv,
    }
    total_queries = {
        "products": select(func.count(Product.id)),
        "sales": select(func.count(Sale.id)),
        "stocks": select(func.count(Stock.id)),
        "purchases": select(func.count(PurchaseOrder.id)),
    }
    if kind not in handlers:
        query = urlencode({"import_status": "error", "import_message": "Неизвестный тип импорта."})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    try:
        new_rows = handlers[kind](db, file.file)
    except ImportValidationError as exc:
        query = urlencode({"import_status": "error", "import_message": str(exc)})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    except Exception as exc:
        query = urlencode({"import_status": "error", "import_message": f"Ошибка импорта файла: {exc}"})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    total_rows = db.scalar(total_queries[kind]) or 0
    query = urlencode(
        {
            "import_status": "ok",
            "import_message": (
                f"Импорт '{kind}' выполнен. Новых строк: {new_rows}. "
                f"Всего записей этого типа в базе: {total_rows}. "
                "Если новых строк 0, файл уже был загружен ранее или строки совпали с существующими."
            ),
        }
    )
    return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/import/onec-xls/{kind}")
async def import_onec_xls(kind: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    handlers = {
        "products": import_onec_products_xls,
        "sales": import_onec_sales_xls,
        "stocks": import_onec_stocks_xls,
        "purchases": import_onec_movements_xls_as_purchases,
    }
    if kind not in handlers:
        query = urlencode({"import_status": "error", "import_message": "Неизвестный тип XLS-импорта."})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    try:
        new_rows = handlers[kind](db, file.file)
    except Exception as exc:
        query = urlencode({"import_status": "error", "import_message": f"Ошибка XLS-импорта: {exc}"})
        return RedirectResponse(url=f"/?{query}", status_code=303)
    query = urlencode(
        {
            "import_status": "ok",
            "import_message": f"XLS-импорт '{kind}' выполнен. Новых строк: {new_rows}.",
        }
    )
    return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/admin/reset-data")
def reset_data(db: Session = Depends(get_db)):
    db.query(RecommendationItem).delete()
    db.query(RecommendationRun).delete()
    db.query(PurchaseOrder).delete()
    db.query(Stock).delete()
    db.query(Sale).delete()
    db.query(Product).delete()
    db.query(Store).delete()
    db.commit()
    query = urlencode({"import_status": "ok", "import_message": "База очищена. Можно загружать реальные данные из 1С."})
    return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/sync/onec")
def sync_onec(db: Session = Depends(get_db)):
    try:
        result = sync_from_onec(db)
        status = "ok" if result.ok else "warn"
        details = ", ".join(f"{key}: {value}" for key, value in result.imported.items())
        message = result.message if not details else f"{result.message} Импортировано: {details}."
    except Exception as exc:
        status = "error"
        message = f"Ошибка синхронизации 1С: {exc}"
    return RedirectResponse(url=f"/?{urlencode({'sync_status': status, 'sync_message': message})}", status_code=303)


@app.post("/forecast")
def create_forecast(
    target_start: date = Form(...),
    target_end: date = Form(...),
    category: str = Form(...),
    with_ai: bool = Form(False),
    db: Session = Depends(get_db),
):
    items = build_statistical_forecast(db, target_start, target_end, category)
    empty_selection_note = ""
    if not items:
        empty_selection_note = (
            f"По запросу '{category}' не найдено позиций с данными. "
            "Если нужен расчет всей категории, выберите вариант с пометкой 'общ', например 'Роза общ'. "
            "Если нужна одна позиция, введите ее точное название."
        )
    try:
        primary_ai = build_primary_ai_recommendation(target_start, target_end, items)
    except Exception as exc:
        primary_ai = {"enabled": False, "note": f"Ошибка первичной AI-рекомендации: {exc}"}
    event_ai = {}
    notes_parts = []
    if empty_selection_note:
        notes_parts.append({"selection_warning": empty_selection_note})
    if primary_ai.get("enabled"):
        notes_parts.append({"primary_recommendation": primary_ai})
    elif primary_ai.get("note"):
        notes_parts.append({"primary_recommendation": primary_ai.get("note")})
    if with_ai:
        try:
            event_ai = build_ai_recommendation(target_start, target_end, items)
        except Exception as exc:
            event_ai = {"enabled": False, "note": f"Ошибка AI-рекомендации с календарем событий: {exc}"}
        notes_parts.append({"event_recommendation": event_ai})
    run = save_recommendation_run(
        db,
        target_start=target_start,
        target_end=target_end,
        category=category,
        items=items,
        notes=json.dumps(notes_parts, ensure_ascii=False, indent=2) if notes_parts else "",
        primary_ai=primary_ai,
        event_ai=event_ai,
    )
    return RedirectResponse(url=f"/runs/{run.id}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def show_run(run_id: int, request: Request, db: Session = Depends(get_db)):
    run = db.get(RecommendationRun, run_id)
    if not run:
        return RedirectResponse(url="/", status_code=303)
    metrics = evaluate_against_actual_purchases(db, run)
    has_event_recommendation = '"event_recommendation"' in (run.notes or "")
    recommendation_summary = {
        "label": _display_category_name(run.category),
        "final_quantity": sum(item.final_quantity for item in run.items),
        "statistical_quantity": sum(item.statistical_quantity for item in run.items),
        "ai_quantity": sum((item.ai_quantity or item.statistical_quantity) for item in run.items),
        "baseline_demand": sum(item.baseline_demand for item in run.items),
        "current_stock": sum(item.current_stock for item in run.items),
        "usable_stock": sum(item.usable_stock for item in run.items),
        "incoming_orders": sum(item.incoming_orders for item in run.items),
        "safety_stock": sum(item.safety_stock for item in run.items),
        "items_count": len(run.items),
    }
    return templates.TemplateResponse(
        "run.html",
        {
            "request": request,
            "run": run,
            "metrics": metrics,
            "has_event_recommendation": has_event_recommendation,
            "recommendation_summary": recommendation_summary,
        },
    )


@app.get("/analytics", response_class=HTMLResponse)
def analytics(
    request: Request,
    category: str = settings.mvp_product_category,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    default_end = date.today()
    default_start = default_end - timedelta(days=90)
    start = start or default_start
    end = end or default_end
    data = build_analytics_data(db, category, start, end)
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "settings": settings,
            "category": category,
            "start": start,
            "end": end,
            "analytics": data,
            "analytics_json": json.dumps(data, ensure_ascii=False),
            "summary": data["summary"],
        },
    )


@app.get("/backtest", response_class=HTMLResponse)
def backtest(
    request: Request,
    category: str = settings.mvp_product_category,
    target_start: date | None = None,
    target_end: date | None = None,
    db: Session = Depends(get_db),
):
    latest_actual_sale_date = db.scalar(
        select(func.max(Sale.sale_date))
        .join(Product, Product.id == Sale.product_id)
        .where(
            Product.sales_category == category,
            Sale.sale_date <= date.today(),
        )
    )
    default_end = latest_actual_sale_date or date.today()
    default_start = default_end - timedelta(days=settings.forecast_period_days - 1)
    target_start = target_start or default_start
    target_end = target_end or default_end
    result = build_backtest(db, target_start, target_end, category)
    return templates.TemplateResponse(
        "backtest.html",
        {
            "request": request,
            "settings": settings,
            "category": category,
            "target_start": target_start,
            "target_end": target_end,
            "result": result,
        },
    )


@app.get("/api/runs/{run_id}")
def api_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(RecommendationRun, run_id)
    if not run:
        return {"error": "not_found"}
    return {
        "id": run.id,
        "target_start_date": run.target_start_date,
        "target_end_date": run.target_end_date,
        "category": run.category,
        "items": [
            {
                "product": item.product.purchase_name,
                "statistical_quantity": item.statistical_quantity,
                "ai_quantity": item.ai_quantity,
                "final_quantity": item.final_quantity,
                "current_stock": item.current_stock,
                "usable_stock": item.usable_stock,
                "stock_snapshot_date": item.stock_snapshot_date,
                "stock_age_days": item.stock_age_days,
                "historical_sold": item.historical_sold,
                "historical_leftover": item.historical_leftover,
                "historical_purchased": item.historical_purchased,
                "historical_purchase_need": item.historical_purchase_need,
                "incoming_orders": item.incoming_orders,
                "expected_next_receipt_date": item.expected_next_receipt_date,
                "expected_next_receipt_note": item.expected_next_receipt_note,
                "baseline_demand": item.baseline_demand,
                "trend_coefficient": item.trend_coefficient,
                "trend_current_sales": item.trend_current_sales,
                "trend_previous_sales": item.trend_previous_sales,
                "trend_current_start": item.trend_current_start,
                "trend_current_end": item.trend_current_end,
                "trend_previous_start": item.trend_previous_start,
                "trend_previous_end": item.trend_previous_end,
                "safety_stock": item.safety_stock,
                "explanation": item.explanation,
            }
            for item in run.items
        ],
    }
