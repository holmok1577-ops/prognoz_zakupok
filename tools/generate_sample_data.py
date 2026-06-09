from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path


OUT = Path("sample_data")
PRODUCT_ID = "rose_montblanc_50"
PRODUCT_NAME = "Роза Эквадор Монблан 50"
CATEGORY = "Роза 50"
STORES = [(f"store_{idx}", f"Магазин {idx}") for idx in range(1, 10)]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ru_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def add_sales_rows(rows: list[dict], day: date, total: int, revenue_per_stem: int) -> None:
    weights = [1.18, 0.96, 1.05, 0.88, 1.14, 0.92, 1.02, 0.84, 1.01]
    raw = [max(1, round(total * weight / sum(weights))) for weight in weights]
    delta = total - sum(raw)
    raw[0] += delta
    for (store_id, store_name), qty in zip(STORES, raw):
        rows.append(
            {
                "date": ru_date(day),
                "store_id": store_id,
                "store_name": store_name,
                "product_id_1c": PRODUCT_ID,
                "product_name": PRODUCT_NAME,
                "category_name": CATEGORY,
                "quantity": qty,
                "revenue": qty * revenue_per_stem,
                "sale_type": "retail",
            }
        )


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_sales() -> list[dict]:
    rows: list[dict] = []

    # Extra baseline for manual checks before the main lookback window.
    april_pattern = [150, 162, 148, 158, 176, 184, 166]
    for idx, day in enumerate(daterange(date(2025, 4, 1), date(2025, 4, 8))):
        add_sales_rows(rows, day, april_pattern[idx % len(april_pattern)], 150)

    # Lookback window for trend: 23.04-17.06.2025, total 11 088 stems.
    # Target comparable period: 18.06-24.06.2025, total 1 540 stems.
    # Business balance for that target period: bought 1 750, sold 1 540, left 210.
    # Scale: 9 stores, roughly 20-35 stems per store per day for one rose category.
    previous_pattern = [180, 195, 170, 190, 215, 230, 206]
    for idx, day in enumerate(daterange(date(2025, 4, 23), date(2025, 6, 17))):
        add_sales_rows(rows, day, previous_pattern[idx % len(previous_pattern)], 150)

    target_2025 = [205, 215, 225, 220, 245, 225, 205]
    for idx, day in enumerate(daterange(date(2025, 6, 18), date(2025, 6, 24))):
        add_sales_rows(rows, day, target_2025[idx], 150)

    # Current trend window: 23.04-17.06.2026, total 12 432 stems.
    # Trend coefficient: about 1.12.
    current_pattern = [200, 218, 190, 212, 240, 258, 236]
    for idx, day in enumerate(daterange(date(2026, 4, 23), date(2026, 6, 17))):
        add_sales_rows(rows, day, current_pattern[idx % len(current_pattern)], 160)

    # Extra history around high-demand flower events for future manual checks.
    event_history = {
        date(2025, 2, 14): 1100,
        date(2025, 3, 6): 1600,
        date(2025, 3, 7): 2450,
        date(2025, 3, 8): 3300,
        date(2025, 9, 1): 1900,
        date(2025, 10, 5): 980,
        date(2026, 2, 14): 1250,
        date(2026, 3, 6): 1780,
        date(2026, 3, 7): 2700,
        date(2026, 3, 8): 3650,
    }
    for day, total in event_history.items():
        add_sales_rows(rows, day, total, 165 if day.year == 2026 else 155)

    return rows


def build_stocks() -> list[dict]:
    leftover_2025 = [29, 23, 25, 20, 27, 22, 24, 18, 22]
    may_2026 = [18, 15, 17, 13, 19, 14, 16, 12, 14]
    current_2026 = [34, 28, 31, 24, 32, 26, 29, 22, 24]
    rows = [
        {
            "date": "08.04.2025",
            "store_id": store_id,
            "store_name": store_name,
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity": qty,
        }
        for (store_id, store_name), qty in zip(STORES, [21, 18, 20, 16, 23, 17, 19, 15, 17])
    ]
    rows.extend(
        [
            {
                "date": "24.06.2025",
                "store_id": store_id,
                "store_name": store_name,
                "product_id_1c": PRODUCT_ID,
                "product_name": PRODUCT_NAME,
                "category_name": CATEGORY,
                "quantity": qty,
            }
            for (store_id, store_name), qty in zip(STORES, leftover_2025)
        ]
    )
    rows.extend(
        [
        {
            "date": "30.04.2026",
            "store_id": store_id,
            "store_name": store_name,
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity": qty,
        }
        for (store_id, store_name), qty in zip(STORES, may_2026)
        ]
    )
    rows.extend(
        [
            {
                "date": "17.06.2026",
                "store_id": store_id,
                "store_name": store_name,
                "product_id_1c": PRODUCT_ID,
                "product_name": PRODUCT_NAME,
                "category_name": CATEGORY,
                "quantity": qty,
            }
            for (store_id, store_name), qty in zip(STORES, current_2026)
        ]
    )
    return rows


def build_purchases() -> list[dict]:
    return [
        {
            "order_date": "10.03.2025",
            "delivery_date": "01.04.2025",
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity_ordered": 1460,
            "quantity_received": 1460,
            "supplier": "Тестовый поставщик",
        },
        {
            "order_date": "25.05.2025",
            "delivery_date": "18.06.2025",
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity_ordered": 1750,
            "quantity_received": 1750,
            "supplier": "Тестовый поставщик",
        },
        {
            "order_date": "01.06.2026",
            "delivery_date": "20.06.2026",
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity_ordered": 1700,
            "quantity_received": 1700,
            "supplier": "Тестовый поставщик",
        },
        {
            "order_date": "10.02.2026",
            "delivery_date": "14.02.2026",
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity_ordered": 1250,
            "quantity_received": 1250,
            "supplier": "Тестовый поставщик",
        },
        {
            "order_date": "15.02.2026",
            "delivery_date": "06.03.2026",
            "product_id_1c": PRODUCT_ID,
            "product_name": PRODUCT_NAME,
            "category_name": CATEGORY,
            "quantity_ordered": 8200,
            "quantity_received": 8200,
            "supplier": "Тестовый поставщик",
        },
    ]


def main() -> None:
    write_csv(
        OUT / "products.csv",
        ["onec_id", "purchase_name", "sales_category", "flower_type", "variety", "height_cm", "allocation_weight"],
        [
            {
                "onec_id": PRODUCT_ID,
                "purchase_name": PRODUCT_NAME,
                "sales_category": CATEGORY,
                "flower_type": "rose",
                "variety": "Монблан",
                "height_cm": 50,
                "allocation_weight": 1,
            }
        ],
    )
    write_csv(
        OUT / "sales.csv",
        ["date", "store_id", "store_name", "product_id_1c", "product_name", "category_name", "quantity", "revenue", "sale_type"],
        build_sales(),
    )
    write_csv(
        OUT / "stocks.csv",
        ["date", "store_id", "store_name", "product_id_1c", "product_name", "category_name", "quantity"],
        build_stocks(),
    )
    write_csv(
        OUT / "purchases.csv",
        ["order_date", "delivery_date", "product_id_1c", "product_name", "category_name", "quantity_ordered", "quantity_received", "supplier"],
        build_purchases(),
    )


if __name__ == "__main__":
    main()
