from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.importer import (
    import_products_rows,
    import_purchases_rows,
    import_sales_rows,
    import_stocks_rows,
)


@dataclass(frozen=True)
class OneCSyncResult:
    ok: bool
    message: str
    imported: dict[str, int]


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        return payload["value"]
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    raise ValueError("1C response must be a JSON list or an object with value/rows list")


def sync_from_onec(db: Session) -> OneCSyncResult:
    settings = get_settings()
    paths = {
        "products": settings.onec_products_path,
        "sales": settings.onec_sales_path,
        "stocks": settings.onec_stocks_path,
        "purchases": settings.onec_purchases_path,
    }

    if settings.onec_connection_type not in {"http", "odata"}:
        return OneCSyncResult(
            ok=False,
            message="Автовыгрузка не настроена: ONEC_CONNECTION_TYPE должен быть http или odata.",
            imported={},
        )
    if not settings.onec_base_url:
        return OneCSyncResult(ok=False, message="Не задан ONEC_BASE_URL.", imported={})
    if not any(paths.values()):
        return OneCSyncResult(
            ok=False,
            message="Не заданы ONEC_PRODUCTS_PATH / ONEC_SALES_PATH / ONEC_STOCKS_PATH / ONEC_PURCHASES_PATH.",
            imported={},
        )

    handlers = {
        "products": import_products_rows,
        "sales": import_sales_rows,
        "stocks": import_stocks_rows,
        "purchases": import_purchases_rows,
    }
    imported: dict[str, int] = {}
    auth = (settings.onec_username, settings.onec_password) if settings.onec_username else None

    with httpx.Client(
        base_url=settings.onec_base_url.rstrip("/") + "/",
        auth=auth,
        timeout=settings.onec_timeout_seconds,
    ) as client:
        for kind, path in paths.items():
            if not path:
                continue
            response = client.get(path.lstrip("/"))
            response.raise_for_status()
            rows = _extract_rows(response.json())
            imported[kind] = handlers[kind](db, rows)

    return OneCSyncResult(ok=True, message="Данные из 1С загружены.", imported=imported)
