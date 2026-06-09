import json
from datetime import date
from pathlib import Path

from openai import OpenAI

from app.core.config import get_settings
from app.services.forecast import ForecastItem
from app.services.event_calendar import demand_events_for_period, format_events_for_prompt


EVENT_INSTRUCTIONS_PATH = Path("app/prompts/flower_demand_events.md")
CUSTOMER_CONTEXT_PATH = Path("app/prompts/customer_context.md")


def _event_instructions() -> str:
    if EVENT_INSTRUCTIONS_PATH.exists():
        return EVENT_INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    return "Используй только переданные события и расчетные данные. Не выдумывай продажи."


def _customer_context() -> str:
    if CUSTOMER_CONTEXT_PATH.exists():
        return CUSTOMER_CONTEXT_PATH.read_text(encoding="utf-8")
    return "Клиент: сеть цветочных магазинов. Розы имеют короткий срок хранения. Не выдумывай числа."


def _forecast_payload(items: list[ForecastItem]) -> list[dict]:
    return [
        {
            "purchase_name": item.product.purchase_name,
            "sales_category": item.product.sales_category,
            "statistical_quantity": item.statistical_quantity,
            "baseline_demand": round(item.baseline_demand, 2),
            "trend_coefficient": round(item.trend_coefficient, 2),
            "trend_current_sales": round(item.trend_current_sales, 2),
            "trend_previous_sales": round(item.trend_previous_sales, 2),
            "trend_current_period": [item.trend_current_start.isoformat(), item.trend_current_end.isoformat()],
            "trend_previous_period": [item.trend_previous_start.isoformat(), item.trend_previous_end.isoformat()],
            "current_stock": round(item.current_stock, 2),
            "usable_stock": round(item.usable_stock, 2),
            "stock_snapshot_date": item.stock_snapshot_date.isoformat() if item.stock_snapshot_date else None,
            "stock_age_days": item.stock_age_days,
            "expected_next_receipt_date": item.expected_next_receipt_date.isoformat() if item.expected_next_receipt_date else None,
            "expected_next_receipt_note": item.expected_next_receipt_note,
            "historical_sold": round(item.historical_sold, 2),
            "historical_leftover": round(item.historical_leftover, 2),
            "historical_purchased": round(item.historical_purchased, 2),
            "historical_purchase_need": round(item.historical_purchase_need, 2),
            "incoming_orders": round(item.incoming_orders, 2),
            "safety_stock": round(item.safety_stock, 2),
            "formula_explanation": item.explanation,
        }
        for item in items
    ]


def _loads_json_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


def _clean_ai_text(value):
    replacements = {
        "наставил закупку": "рассчитал закупку",
        "наставила закупку": "рассчитала закупку",
        "наставили закупку": "рассчитали закупку",
        "скоропортящести": "короткого срока хранения",
        "скоропортимости": "короткого срока хранения",
        "скоропортящийся товар": "товар с коротким сроком хранения",
        "на розы и гвоздики": "на гвоздики",
        "на розы": "на гвоздики",
        "розы не ожидается": "гвоздики ожидается",
    }
    if isinstance(value, dict):
        return {key: _clean_ai_text(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_clean_ai_text(child) for child in value]
    if isinstance(value, str):
        cleaned = value
        for source, target in replacements.items():
            cleaned = cleaned.replace(source, target)
        return cleaned
    return value


def _disabled_result() -> dict:
    return {
        "enabled": False,
        "primary_items": [],
        "event_items": [],
        "events": [],
        "note": "OpenAI отключен. Для AI-рекомендации укажите OPENAI_API_KEY и OPENAI_ENABLED=true.",
    }


def _openai_client() -> OpenAI:
    settings = get_settings()
    kwargs = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return OpenAI(**kwargs)


def _insufficient_history_result(items: list[ForecastItem], key: str) -> dict | None:
    insufficient = [
        item
        for item in items
        if item.historical_sold <= 0 and item.historical_purchase_need <= 0 and item.statistical_quantity <= 0
    ]
    if not insufficient or len(insufficient) < len(items):
        return None
    return {
        "enabled": True,
        "events": [],
        key: [
            {
                "purchase_name": item.product.purchase_name,
                "recommended_quantity": 0,
                "recommendation_text": (
                    "Недостаточно истории за аналогичный период прошлого года, поэтому расчет не подтверждает "
                    "объем закупки. Для рабочего прогноза загрузите продажи за этот период или выберите период, "
                    "по которому есть история."
                ),
                "confidence": "low",
                "risk": "При отсутствии истории модель не должна подменять расчет предположениями.",
            }
            for item in insufficient
        ],
        "general_note": "По части позиций нет исторической базы. Рекомендация не является рабочей закупкой.",
    }


def build_primary_ai_recommendation(target_start: date, target_end: date, items: list[ForecastItem]) -> dict:
    settings = get_settings()
    if not settings.openai_enabled or not settings.openai_api_key:
        return _disabled_result()
    insufficient_result = _insufficient_history_result(items, "primary_items")
    if insufficient_result:
        return insufficient_result

    client = _openai_client()
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты старший аналитик закупок для сети цветочных магазинов. "
                    "Сформулируй первичную рекомендацию человеческим языком, но не меняй числа без причины. "
                    "Пиши деловым русским языком: 'расчет показывает', 'рекомендуем заказать', "
                    "'система рассчитала'. Не используй странные выражения вроде 'наставил закупку'. "
                    "Для риска списания используй формулировку 'из-за короткого срока хранения', "
                    "не пиши 'скоропортящесть' или похожие тяжелые слова. "
                    "Не используй праздники и внешние события в этом режиме. "
                    "Обязательно объясняй рекомендацию через ожидаемый спрос, тренд и страховой запас. "
                    "Если safety_stock больше 0, не пиши, что заказ сделан без излишков или без запаса. "
                    "Остаток прошлого года не вычитай из проданного спроса: он показывает прошлогодний излишек. "
                    "Работай только с переданными расчетами. Верни строгий JSON.\n\n"
                    f"{_customer_context()}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mode": "primary_recommendation_without_events",
                        "target_period": [target_start.isoformat(), target_end.isoformat()],
                        "items": _forecast_payload(items),
                        "expected_json_schema": {
                            "primary_items": [
                                {
                                    "purchase_name": "string",
                                    "recommended_quantity": "number",
                                    "recommendation_text": "string",
                                    "confidence": "low|medium|high",
                                    "risk": "string",
                                }
                            ],
                            "general_note": "string",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    try:
        return {"enabled": True, "events": [], **_clean_ai_text(_loads_json_response(response.output_text))}
    except json.JSONDecodeError:
        return {"enabled": True, "events": [], "raw": response.output_text, "primary_items": []}


def build_ai_recommendation(target_start: date, target_end: date, items: list[ForecastItem]) -> dict:
    settings = get_settings()
    if not settings.openai_enabled or not settings.openai_api_key:
        return _disabled_result()
    insufficient_result = _insufficient_history_result(items, "event_items")
    if insufficient_result:
        return insufficient_result

    events = demand_events_for_period(target_start, target_end)
    client = _openai_client()
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "Ты помощник старшего кладовщика сети цветочных магазинов. "
                    "Не выдумывай продажи и поставки. Работай только поверх статистического прогноза, остатков, "
                    "уже заказанных поставок из 1С и календаря событий. Верни строгий JSON.\n\n"
                    "Пиши строго про ту номенклатуру, которая указана в purchase_name. "
                    "Если позиция — гвоздика, не упоминай розы вообще. "
                    "День Победы для гвоздик является значимым событием из-за возложения цветов к памятникам; "
                    "не называй его незначительным для гвоздик без сильного числового основания в остатках и продажах.\n\n"
                    "Пиши деловым русским языком: 'расчет показывает', 'рекомендуем заказать', "
                    "'система рассчитала'. Не используй странные выражения вроде 'наставил закупку'.\n\n"
                    "Для риска списания используй формулировку 'из-за короткого срока хранения', "
                    "не пиши 'скоропортящесть' или похожие тяжелые слова.\n\n"
                    "Объясняй, какая часть рекомендации покрывает ожидаемый спрос, а какая является запасом "
                    "или праздничной корректировкой. Не называй заказ безызлишковым, если есть запас.\n\n"
                    f"{_customer_context()}\n\n"
                    f"{_event_instructions()}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "target_period": [target_start.isoformat(), target_end.isoformat()],
                        "city": settings.default_city,
                        "region": settings.default_region,
                        "events": format_events_for_prompt(events),
                        "items": _forecast_payload(items),
                        "expected_json_schema": {
                            "event_items": [
                                {
                                    "purchase_name": "string",
                                    "recommended_quantity": "number",
                                    "adjustment_percent": "number",
                                    "recommendation_text": "string",
                                    "risk": "string",
                                    "used_events": ["string"],
                                }
                            ],
                            "general_note": "string",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    text = response.output_text
    try:
        return {"enabled": True, "events": format_events_for_prompt(events), **_clean_ai_text(_loads_json_response(text))}
    except json.JSONDecodeError:
        return {"enabled": True, "events": format_events_for_prompt(events), "raw": text, "event_items": []}
