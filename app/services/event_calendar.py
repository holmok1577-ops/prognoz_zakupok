from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DemandEvent:
    name: str
    start: date
    end: date
    impact: str
    typical_adjustment: str
    note: str


FIXED_EVENTS = [
    (1, 1, "Новый год", "low", "+0-10%", "Основной спрос обычно до праздника; не повышать без статистики."),
    (1, 7, "Рождество", "medium", "+5-15%", "Умеренный спрос на подарочные букеты и композиции."),
    (1, 25, "Татьянин день / День студента", "low", "+0-10%", "Небольшой или умеренный рост спроса."),
    (2, 14, "День святого Валентина", "high", "+20-60%", "Сильный рост спроса на розы."),
    (2, 23, "День защитника Отечества", "medium", "+5-20%", "Умеренный рост спроса на букеты."),
    (3, 8, "Международный женский день", "critical", "+50-200%", "Главный годовой пик спроса на цветы."),
    (5, 9, "День Победы", "high", "+5-25% для гвоздик", "Заметно влияет на гвоздики: цветы покупают для возложения к памятникам и мемориалам."),
    (6, 1, "День защиты детей", "low", "+0-10%", "Небольшой рост подарочных покупок."),
    (7, 8, "День семьи, любви и верности", "medium", "+5-15%", "Умеренный спрос на подарочные букеты."),
    (9, 1, "День знаний", "high", "+30-100%", "Сильный краткосрочный школьный спрос."),
    (9, 27, "День воспитателя", "medium", "+5-20%", "Рост спроса на букеты для детских садов."),
    (10, 1, "День пожилого человека", "low", "+0-10%", "Небольшой рост подарочных букетов."),
    (10, 5, "День учителя", "high", "+15-45%", "Заметный рост спроса на букеты для учителей."),
    (12, 31, "Новый год", "medium", "+0-15%", "Спрос на подарочные букеты и композиции перед праздником."),
]


def _last_sunday(year: int, month: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != 6:
        current -= timedelta(days=1)
    return current


def _event_window(day: date, before: int = 3, after: int = 0) -> tuple[date, date]:
    return day - timedelta(days=before), day + timedelta(days=after)


def demand_events_for_period(start: date, end: date) -> list[DemandEvent]:
    lookup_start = start - timedelta(days=7)
    lookup_end = end + timedelta(days=1)
    events: list[DemandEvent] = []

    for year in range(lookup_start.year, lookup_end.year + 1):
        for month, day, name, impact, adjustment, note in FIXED_EVENTS:
            event_day = date(year, month, day)
            window_start, window_end = _event_window(event_day)
            if window_start <= lookup_end and window_end >= lookup_start:
                events.append(DemandEvent(name, event_day, event_day, impact, adjustment, note))

        mothers_day = _last_sunday(year, 11)
        window_start, window_end = _event_window(mothers_day)
        if window_start <= lookup_end and window_end >= lookup_start:
            events.append(
                DemandEvent(
                    "День матери",
                    mothers_day,
                    mothers_day,
                    "high",
                    "+10-35%",
                    "Рост спроса на подарочные букеты; зависит от рекламы.",
                )
            )

        for approximate_day, name, impact, adjustment, note in [
            (
                date(year, 5, 20),
                "Последний звонок",
                "high",
                "+10-30%",
                "Обычно приходится на 20-25 мая, точную дату нужно уточнять ежегодно.",
            ),
            (
                date(year, 6, 20),
                "Выпускные",
                "high",
                "+10-35%",
                "Июньский период выпускных; даты отличаются по школам и вузам.",
            ),
            (
                date(year, 6, 15),
                "Свадебный сезон",
                "medium",
                "+5-20%",
                "Май-сентябрь может повышать спрос на розы и букеты.",
            ),
        ]:
            window_start, window_end = _event_window(approximate_day, before=10, after=10)
            if window_start <= lookup_end and window_end >= lookup_start:
                events.append(DemandEvent(name, window_start, window_end, impact, adjustment, note))

    events.append(
        DemandEvent(
            "Сагаалган / Белый месяц",
            start,
            end,
            "manual_check",
            "+5-25%",
            "Дата меняется каждый год; для Бурятии нужно уточнять актуальный календарь.",
        )
    )
    events.append(
        DemandEvent(
            "День города / локальные мероприятия",
            start,
            end,
            "manual_check",
            "+5-20%",
            "Дата и влияние зависят от города и программы мероприятий; требуется ручная проверка.",
        )
    )
    return events


def format_events_for_prompt(events: list[DemandEvent]) -> list[dict[str, str]]:
    return [
        {
            "name": event.name,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "impact": event.impact,
            "typical_adjustment": event.typical_adjustment,
            "note": event.note,
        }
        for event in events
    ]
