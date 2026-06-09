import csv
import hashlib
from datetime import date, datetime
from io import TextIOWrapper
from collections.abc import Iterable
from typing import Any, BinaryIO

from dateutil.parser import parse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, PurchaseOrder, Sale, Stock, Store


class ImportValidationError(ValueError):
    pass


REQUIRED_COLUMNS = {
    "products": {"onec_id"},
    "sales": {"date", "product_id_1c", "quantity"},
    "stocks": {"date", "product_id_1c", "quantity"},
    "purchases": {"order_date", "delivery_date", "product_id_1c", "quantity_ordered"},
}


def _as_date(value: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("Дата не заполнена.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%d.%m.%Y").date()
    except ValueError:
        pass
    return parse(value, dayfirst=True).date()


def _as_float(value: str | int | float | None) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return float(str(value).replace(" ", "").replace(",", "."))


def _hash_row(row: dict[str, Any]) -> str:
    payload = "|".join(f"{key}={row.get(key, '')}" for key in sorted(row))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reader(file: BinaryIO):
    text = TextIOWrapper(file, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def _validate_csv_headers(reader: csv.DictReader, kind: str) -> None:
    headers = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS[kind] - headers)
    if missing:
        present = ", ".join(reader.fieldnames or []) or "нет заголовков"
        raise ImportValidationError(
            f"Файл для импорта '{kind}' не подходит: не хватает колонок {', '.join(missing)}. "
            f"Найдены колонки: {present}."
        )


def _validate_row(row: dict[str, Any], kind: str) -> None:
    missing = sorted(REQUIRED_COLUMNS[kind] - set(row))
    if missing:
        raise ImportValidationError(
            f"Данные для импорта '{kind}' не подходят: не хватает колонок {', '.join(missing)}."
        )


def get_or_create_product(
    db: Session,
    *,
    onec_id: str,
    purchase_name: str,
    sales_category: str,
    flower_type: str = "",
    variety: str = "",
    height_cm: int | None = None,
    allocation_weight: float = 1.0,
) -> Product:
    product = db.scalar(select(Product).where(Product.onec_id == onec_id))
    if product:
        product.purchase_name = purchase_name or product.purchase_name
        product.sales_category = sales_category or product.sales_category
        product.flower_type = flower_type or product.flower_type
        product.variety = variety or product.variety
        product.height_cm = height_cm if height_cm is not None else product.height_cm
        product.allocation_weight = allocation_weight or product.allocation_weight
        return product

    product = Product(
        onec_id=onec_id,
        purchase_name=purchase_name,
        sales_category=sales_category,
        flower_type=flower_type,
        variety=variety,
        height_cm=height_cm,
        allocation_weight=allocation_weight,
    )
    db.add(product)
    db.flush()
    return product


def get_or_create_store(db: Session, *, onec_id: str, name: str) -> Store | None:
    if not onec_id and not name:
        return None
    key = onec_id or name
    store = db.scalar(select(Store).where(Store.onec_id == key))
    if store:
        store.name = name or store.name
        return store
    store = Store(onec_id=key, name=name or key)
    db.add(store)
    db.flush()
    return store


def import_products_csv(db: Session, file: BinaryIO) -> int:
    reader = _reader(file)
    _validate_csv_headers(reader, "products")
    return import_products_rows(db, reader)


def import_products_rows(db: Session, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        _validate_row(row, "products")
        height = row.get("height_cm") or row.get("height") or ""
        get_or_create_product(
            db,
            onec_id=row["onec_id"],
            purchase_name=row.get("purchase_name") or row.get("product_name") or row["onec_id"],
            sales_category=row.get("sales_category") or row.get("category") or "",
            flower_type=row.get("flower_type", ""),
            variety=row.get("variety", ""),
            height_cm=int(height) if height else None,
            allocation_weight=_as_float(row.get("allocation_weight", 1)),
        )
        count += 1
    db.commit()
    return count


def import_sales_csv(db: Session, file: BinaryIO) -> int:
    reader = _reader(file)
    _validate_csv_headers(reader, "sales")
    return import_sales_rows(db, reader)


def import_sales_rows(db: Session, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        _validate_row(row, "sales")
        product = get_or_create_product(
            db,
            onec_id=row["product_id_1c"],
            purchase_name=row.get("product_name", row["product_id_1c"]),
            sales_category=row.get("category_name", row.get("product_name", "")),
        )
        store = get_or_create_store(
            db,
            onec_id=row.get("store_id", ""),
            name=row.get("store_name", ""),
        )
        source_hash = _hash_row(row)
        exists = db.scalar(select(Sale.id).where(Sale.source_row_hash == source_hash))
        if exists:
            continue
        db.add(
            Sale(
                sale_date=_as_date(row["date"]),
                store_id=store.id if store else None,
                product_id=product.id,
                quantity=_as_float(row.get("quantity")),
                revenue=_as_float(row.get("revenue")),
                sale_type=row.get("sale_type", "unknown") or "unknown",
                source_row_hash=source_hash,
            )
        )
        count += 1
    db.commit()
    return count


def import_stocks_csv(db: Session, file: BinaryIO) -> int:
    reader = _reader(file)
    _validate_csv_headers(reader, "stocks")
    return import_stocks_rows(db, reader)


def import_stocks_rows(db: Session, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        _validate_row(row, "stocks")
        product = get_or_create_product(
            db,
            onec_id=row["product_id_1c"],
            purchase_name=row.get("product_name", row["product_id_1c"]),
            sales_category=row.get("category_name", row.get("product_name", "")),
        )
        store = get_or_create_store(db, onec_id=row.get("store_id", ""), name=row.get("store_name", ""))
        stock_date = _as_date(row["date"])
        existing = db.scalar(
            select(Stock).where(
                Stock.stock_date == stock_date,
                Stock.store_id == (store.id if store else None),
                Stock.product_id == product.id,
            )
        )
        if existing:
            existing.quantity = _as_float(row.get("quantity"))
        else:
            db.add(
                Stock(
                    stock_date=stock_date,
                    store_id=store.id if store else None,
                    product_id=product.id,
                    quantity=_as_float(row.get("quantity")),
                )
            )
            count += 1
    db.commit()
    return count


def import_purchases_csv(db: Session, file: BinaryIO) -> int:
    reader = _reader(file)
    _validate_csv_headers(reader, "purchases")
    return import_purchases_rows(db, reader)


def import_purchases_rows(db: Session, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        _validate_row(row, "purchases")
        product = get_or_create_product(
            db,
            onec_id=row["product_id_1c"],
            purchase_name=row.get("product_name", row["product_id_1c"]),
            sales_category=row.get("category_name", row.get("product_name", "")),
        )
        source_hash = _hash_row(row)
        exists = db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.source_row_hash == source_hash))
        if exists:
            continue
        db.add(
            PurchaseOrder(
                order_date=_as_date(row["order_date"]),
                delivery_date=_as_date(row["delivery_date"]),
                product_id=product.id,
                quantity_ordered=_as_float(row.get("quantity_ordered")),
                quantity_received=_as_float(row.get("quantity_received")),
                supplier=row.get("supplier", ""),
                source_row_hash=source_hash,
            )
        )
        count += 1
    db.commit()
    return count
