import re
from datetime import date, datetime
from typing import BinaryIO

import xlrd
from sqlalchemy.orm import Session

from app.services.importer import (
    import_products_rows,
    import_purchases_rows,
    import_sales_rows,
    import_stocks_rows,
)


def import_onec_products_xls(db: Session, file: BinaryIO) -> int:
    sheet = _first_sheet(file)
    headers = [_cell_text(sheet, 0, col) for col in range(sheet.ncols)]
    name_idx = _find_header(headers, "Наименование")
    type_idx = _find_header(headers, "Вид номенклатуры")
    full_name_idx = _find_header(headers, "Наименование полное")
    if name_idx is None:
        raise ValueError("Не найдена колонка 'Наименование' в номенклатуре.")

    rows = []
    for row_idx in range(1, sheet.nrows):
        name = _cell_text(sheet, row_idx, name_idx)
        if not name:
            continue
        product_type = _cell_text(sheet, row_idx, type_idx)
        flower_type = _flower_type_for_name(name, product_type)
        if not flower_type:
            continue
        full_name = _cell_text(sheet, row_idx, full_name_idx) or name
        rows.append(
            {
                "onec_id": name,
                "purchase_name": name,
                "product_name": full_name,
                "sales_category": _sales_category_for_name(name),
                "category": _sales_category_for_name(name),
                "flower_type": flower_type,
                "variety": name,
                "height_cm": "",
                "allocation_weight": 1,
            }
        )
    return import_products_rows(db, rows)


def import_onec_sales_xls(db: Session, file: BinaryIO) -> int:
    sheet = _first_sheet(file)
    header_row = _find_row_starting(sheet, "Период день")
    if header_row is None:
        raise ValueError("Не найден заголовок 'Период день' в отчете продаж.")
    store_name = _filtered_store_name(sheet)
    store_layout = _sales_store_layout(sheet, header_row)
    rows = []
    for row_idx in range(header_row + 2, sheet.nrows):
        sale_date = _cell_date(sheet, row_idx, 0)
        if not sale_date:
            continue
        if store_layout:
            product_name, stores = store_layout
            for col_idx, column_store_name in stores:
                quantity = _cell_float(sheet, row_idx, col_idx)
                if quantity == 0:
                    continue
                rows.append(
                    {
                        "date": sale_date.isoformat(),
                        "store_id": column_store_name,
                        "store_name": column_store_name,
                        "product_id_1c": product_name,
                        "product_name": product_name,
                        "category_name": _sales_category_for_name(product_name),
                        "quantity": quantity,
                        "revenue": 0,
                        "sale_type": "retail",
                    }
                )
            continue
        for col_idx, product_name in _product_columns(sheet, header_row):
            quantity = _cell_float(sheet, row_idx, col_idx)
            if quantity == 0:
                continue
            rows.append(
                {
                    "date": sale_date.isoformat(),
                    "store_id": store_name,
                    "store_name": store_name,
                    "product_id_1c": product_name,
                    "product_name": product_name,
                    "category_name": _sales_category_for_name(product_name),
                    "quantity": quantity,
                    "revenue": 0,
                    "sale_type": "retail",
                }
            )
    return import_sales_rows(db, rows)


def import_onec_stocks_xls(db: Session, file: BinaryIO) -> int:
    sheet = _first_sheet(file)
    header_row = _find_row_starting(sheet, "Номенклатура")
    if header_row is None:
        raise ValueError("Не найден заголовок 'Номенклатура' в отчете остатков.")
    stores = _store_columns(sheet, header_row)
    current_product = ""
    rows = []
    for row_idx in range(header_row + 2, sheet.nrows):
        first = _cell_text(sheet, row_idx, 0)
        row_date = _cell_date(sheet, row_idx, 0)
        if first and not row_date:
            current_product = first
            continue
        if not row_date or not current_product:
            continue
        for col_idx, store_name in stores:
            quantity = _cell_float(sheet, row_idx, col_idx)
            if quantity == 0:
                continue
            rows.append(
                {
                    "date": row_date.isoformat(),
                    "store_id": store_name,
                    "store_name": store_name,
                    "product_id_1c": current_product,
                    "product_name": current_product,
                    "category_name": _sales_category_for_name(current_product),
                    "quantity": quantity,
                }
            )
    return import_stocks_rows(db, rows)


def import_onec_movements_xls_as_purchases(db: Session, file: BinaryIO) -> int:
    sheet = _first_sheet(file)
    header_row = _find_row_starting(sheet, "Регистратор")
    if header_row is None:
        raise ValueError("Не найден заголовок 'Регистратор' в отчете движений.")
    incoming_col = _find_movement_col(sheet, header_row + 1, "Приход")
    if incoming_col is None:
        raise ValueError("Не найдена колонка 'Приход' в отчете движений.")
    final_stock_col = _find_movement_col(sheet, header_row + 1, "Конечный остаток")
    if final_stock_col is None:
        raise ValueError("Не найдена колонка 'Конечный остаток' в отчете движений.")
    product_name = _filtered_product_name(sheet) or "Гвоздика"
    store_name = _filtered_store_name(sheet) or "Движения 1С"

    purchase_rows = []
    stock_by_date: dict[date, float] = {}
    for row_idx in range(header_row + 2, sheet.nrows):
        registrar = _cell_text(sheet, row_idx, 0)
        movement_date = _date_from_registrar(registrar)
        if not movement_date:
            continue
        quantity = _cell_float(sheet, row_idx, incoming_col)
        if quantity > 0:
            purchase_rows.append(
                {
                    "order_date": movement_date.isoformat(),
                    "delivery_date": movement_date.isoformat(),
                    "product_id_1c": product_name,
                    "product_name": product_name,
                    "category_name": _sales_category_for_name(product_name),
                    "quantity_ordered": quantity,
                    "quantity_received": quantity,
                    "supplier": registrar.split(" от ", 1)[0],
                    "source_line": str(row_idx),
                }
            )
        if _cell_text(sheet, row_idx, final_stock_col):
            stock_by_date[movement_date] = _cell_float(sheet, row_idx, final_stock_col)

    stock_rows = [
        {
            "date": movement_date.isoformat(),
            "store_id": f"movement:{store_name}",
            "store_name": store_name,
            "product_id_1c": product_name,
            "product_name": product_name,
            "category_name": _sales_category_for_name(product_name),
            "quantity": quantity,
        }
        for movement_date, quantity in stock_by_date.items()
    ]
    purchases_count = import_purchases_rows(db, purchase_rows)
    stocks_count = import_stocks_rows(db, stock_rows)
    return purchases_count + stocks_count


def _first_sheet(file: BinaryIO):
    file.seek(0)
    book = xlrd.open_workbook(file_contents=file.read())
    return book.sheet_by_index(0)


def _cell_text(sheet, row: int, col: int | None) -> str:
    if col is None or row >= sheet.nrows or col >= sheet.ncols:
        return ""
    value = sheet.cell_value(row, col)
    if value in ("", None):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _cell_float(sheet, row: int, col: int | None) -> float:
    if col is None or row >= sheet.nrows or col >= sheet.ncols:
        return 0.0
    value = sheet.cell_value(row, col)
    if value in ("", None):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return float(str(value).replace(" ", "").replace(",", "."))


def _cell_date(sheet, row: int, col: int) -> date | None:
    if row >= sheet.nrows or col >= sheet.ncols:
        return None
    cell = sheet.cell(row, col)
    value = cell.value
    if value in ("", None):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        return datetime(*xlrd.xldate_as_tuple(value, sheet.book.datemode)).date()
    if isinstance(value, str):
        match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", value.strip())
        if match:
            day, month, year = map(int, match.groups())
            return date(year, month, day)
    return None


def _find_header(headers: list[str], name: str) -> int | None:
    for idx, header in enumerate(headers):
        if header == name:
            return idx
    return None


def _find_row_starting(sheet, text: str) -> int | None:
    for row_idx in range(sheet.nrows):
        if _cell_text(sheet, row_idx, 0) == text:
            return row_idx
    return None


def _product_columns(sheet, header_row: int) -> list[tuple[int, str]]:
    products = []
    for col_idx in range(1, sheet.ncols):
        name = _cell_text(sheet, header_row, col_idx)
        metric = _cell_text(sheet, header_row + 1, col_idx)
        if not name or name == "Итого" or metric != "Количество товаров":
            continue
        products.append((col_idx, name))
    return products


def _sales_store_layout(sheet, header_row: int) -> tuple[str, list[tuple[int, str]]] | None:
    first_metric_col = None
    for col_idx in range(1, sheet.ncols):
        name = _cell_text(sheet, header_row, col_idx)
        metric = _cell_text(sheet, header_row + 1, col_idx)
        if name and metric == "Количество товаров":
            first_metric_col = col_idx
            break
    if first_metric_col is None:
        return None

    product_name = _cell_text(sheet, header_row, first_metric_col)
    if not _flower_type_for_name(product_name):
        return None

    stores = []
    for col_idx in range(first_metric_col + 1, sheet.ncols):
        name = _cell_text(sheet, header_row, col_idx)
        metric = _cell_text(sheet, header_row + 1, col_idx)
        if not name or metric != "Количество товаров":
            continue
        if name == "Итого":
            break
        stores.append((col_idx, name))
    return (product_name, stores) if stores else None


def _store_columns(sheet, header_row: int) -> list[tuple[int, str]]:
    stores = []
    for col_idx in range(1, sheet.ncols):
        name = _cell_text(sheet, header_row, col_idx)
        metric = _cell_text(sheet, header_row + 1, col_idx)
        if not name or name == "Итого" or "Количество" not in metric:
            continue
        stores.append((col_idx, name))
    return stores


def _find_movement_col(sheet, row_idx: int, header: str) -> int | None:
    for col_idx in range(sheet.ncols):
        if _cell_text(sheet, row_idx, col_idx) == header:
            return col_idx
    return None


def _filtered_product_name(sheet) -> str:
    for row_idx in range(min(sheet.nrows, 8)):
        text = " ".join(_cell_text(sheet, row_idx, col_idx) for col_idx in range(sheet.ncols))
        match = re.search(r'Номенклатура Равно "([^"]+)"', text)
        if match:
            return match.group(1)
    return ""


def _filtered_store_name(sheet) -> str:
    for row_idx in range(min(sheet.nrows, 8)):
        text = " ".join(_cell_text(sheet, row_idx, col_idx) for col_idx in range(sheet.ncols))
        match = re.search(r'Магазин Равно "([^"]+)"', text)
        if match:
            return match.group(1)
    return ""


def _date_from_registrar(text: str) -> date | None:
    match = re.search(r" от (\d{2})\.(\d{2})\.(\d{4})", text)
    if not match:
        return None
    day, month, year = map(int, match.groups())
    return date(year, month, day)


def _sales_category_for_name(name: str) -> str:
    lowered = name.lower()
    if "гвоздик" in lowered:
        return "Гвоздика"
    if "роза" in lowered:
        return "Роза"
    if "хризантем" in lowered:
        return "Хризантема"
    return name


def _flower_type_for_name(name: str, product_type: str = "") -> str:
    lowered = f"{name} {product_type}".lower()
    if "гвоздик" in lowered:
        return "carnation"
    if "роза" in lowered:
        return "rose"
    if "хризантем" in lowered:
        return "chrysanthemum"
    return ""
