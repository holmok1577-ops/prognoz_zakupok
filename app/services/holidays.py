from datetime import date


RU_HOLIDAYS = [
    (1, 1, "Новогодние праздники"),
    (1, 7, "Рождество"),
    (2, 14, "День святого Валентина"),
    (2, 23, "День защитника Отечества"),
    (3, 8, "Международный женский день"),
    (5, 9, "День Победы"),
    (9, 1, "День знаний"),
    (10, 5, "День учителя"),
    (11, 25, "День матери"),
]


def upcoming_holidays(start: date, end: date) -> list[str]:
    events: list[str] = []
    for year in range(start.year, end.year + 1):
        for month, day, name in RU_HOLIDAYS:
            current = date(year, month, day)
            if start <= current <= end:
                events.append(f"{current.isoformat()}: {name}")
    return events
