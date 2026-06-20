# Flower Forecast

Пилотная версия FastAPI-приложения для прогноза закупок цветочного магазина.

Система загружает выгрузки из 1С, считает спрос по доступной истории продаж, остаткам и поступлениям, формирует рекомендацию по закупке и позволяет проверить качество рекомендации на историческом периоде.

## Текущий статус

Проект находится в пилотной версии. Основной рабочий сценарий:

1. Загрузить XLS-выгрузки из 1С:
   - номенклатура;
   - продажи;
   - остатки;
   - поступления.
2. Выбрать, что считать:
   - точную номенклатуру, например `Роза Эквадор 50`;
   - группу с суффиксом `общ`, например `Роза общ` или `Гвоздика общ`.
3. Сформировать прогноз закупки на выбранный период.
4. Проверить результат в разделах:
   - `Аналитика`;
   - `Проверка на истории`.

CSV-импорт сохранен в backend для совместимости и тестов, но не показывается на главной странице, потому что заказчик работает с XLS-выгрузками из 1С.

## Возможности

- импорт XLS из 1С;
- поддержка продаж и остатков по магазинам, если такие колонки есть в выгрузке;
- расчет группы через суффикс `общ`;
- расчет отдельной номенклатуры по точному названию;
- статистический прогноз по прошлым периодам;
- отдельная логика для новых позиций с короткой историей;
- месячный/периодный тренд для позиций без годовой истории;
- учет срока хранения цветов;
- учет свежего остатка только если он доживает до периода поставки;
- учет поступлений как фактических движений товара;
- календарь событий и праздников;
- AI-пояснение рекомендации через OpenAI-compatible API;
- раздел аналитики продаж и остатков;
- раздел проверки на истории;
- сравнение рекомендации с фактическими продажами, закупками и остатками;
- Docker-деплой;
- production compose;
- healthcheck;
- ежедневный backup SQLite на сервере;
- авторизация в приложении;
- админ-панель с backup/restore и сменой пароля;
- внешний доступ через nginx и домен.

## Важная логика выбора номенклатуры

`Роза` и `Роза общ` означают разные вещи.

- `Роза` ищет точную позицию `Роза`. Если такой позиции нет, расчет будет пустым.
- `Роза общ` считает все позиции категории `Роза`.
- `Гвоздика кустовая` считает только точную позицию.
- `Гвоздика общ` считает все позиции категории `Гвоздика`.

Это сделано специально, чтобы ввод `Роза` случайно не раскрывал всю категорию и не давал огромный прогноз не по той позиции.

Если в настройках указана голая категория, например `Роза`, а в данных есть `Роза общ`, приложение использует `Роза общ` как дефолт для ссылок на аналитику и проверку на истории.

## AI и интернет

Модель `gpt-4.1-mini` сама не ходит в интернет. В пилоте интернет-поиск не используется как свободный браузинг модели.

Для событий и праздников используется локальная календарная логика:

- `app/services/event_calendar.py`;
- `app/prompts/flower_demand_events.md`.

AI получает уже рассчитанные числа от приложения и должен дать понятное объяснение: почему рекомендуется такой объем, какие есть риски, мало ли данных для анализа, есть ли праздничный фактор.

Для работы через ProxyAPI используется OpenAI-compatible endpoint:

```env
OPENAI_ENABLED=true
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
OPENAI_MODEL=gpt-4.1-mini

ADMIN_AUTH_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_INITIAL_PASSWORD=replace_me_once
ADMIN_SESSION_DAYS=7
ADMIN_BACKUP_DIR=/app/backups
```

## Быстрый старт локально

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Открыть:

```text
http://127.0.0.1:8000
```

## Запуск через Docker локально

```bash
copy .env.example .env
docker compose up --build
```

Локальное приложение будет доступно на:

```text
http://127.0.0.1:8000
```

## Production-деплой

Для сервера используется:

```bash
docker compose -f docker-compose.prod.yml up -d --build flower-forecast
```

Production compose:

- читает `.env`;
- хранит SQLite в `./data/app.db`;
- публикует приложение только на `127.0.0.1:8001`;
- наружу приложение отдается через nginx;
- включает `restart: unless-stopped`;
- ограничивает память контейнера;
- включает healthcheck.

На текущем сервере внешний вход идет через:

```text
https://wwwholmok1577.ru/
```

Прямой порт приложения снаружи закрыт firewall-ом.

## 24/7 и эксплуатация

На сервере настроено:

- Docker service enabled;
- контейнер `flower-forecast` с `restart: unless-stopped`;
- healthcheck Docker;
- `prognoz-healthcheck.timer`;
- `prognoz-backup.timer`;
- UFW firewall.

Проверка статуса:

```bash
docker ps --filter name=flower-forecast
curl http://127.0.0.1:8001/health
systemctl status prognoz-healthcheck.timer
systemctl status prognoz-backup.timer
ufw status verbose
```

Health-monitor:

- каждые 5 минут проверяет `http://127.0.0.1:8001/health`;
- при ошибке перезапускает контейнер.

Backup:

- ежедневный backup SQLite в `03:30 UTC`;
- путь: `/opt/backups/prognoz_zakupok`;
- хранение: 14 дней;
- backup создается через SQLite backup API и сжимается в `.gz`.

Ручной backup:

```bash
/usr/local/bin/prognoz-backup.sh
```

## Что внести в `.env`

Минимальный production-набор:

```env
APP_NAME="Flower Purchase Forecast"
APP_ENV=production
APP_DEBUG=false
APP_SECRET_KEY=replace_me

DATABASE_URL=sqlite:///./data/app.db

ONEC_CONNECTION_TYPE=csv
ONEC_BASE_URL=
ONEC_USERNAME=
ONEC_PASSWORD=
ONEC_TIMEOUT_SECONDS=60
ONEC_PRODUCTS_PATH=
ONEC_SALES_PATH=
ONEC_STOCKS_PATH=
ONEC_PURCHASES_PATH=

OPENAI_ENABLED=true
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
OPENAI_MODEL=gpt-4.1-mini

DEFAULT_REGION=RU-BU
DEFAULT_CITY=Ulan-Ude
DEFAULT_TIMEZONE=Asia/Irkutsk

MVP_PRODUCT_CATEGORY="Роза"
FORECAST_LEAD_DAYS=21
FORECAST_PERIOD_DAYS=7
TREND_LOOKBACK_WEEKS=16
SAFETY_STOCK_PERCENT=5
USABLE_STOCK_PERCENT=25
STOCK_SHELF_LIFE_DAYS=7
```

Примечание: переменная `MVP_PRODUCT_CATEGORY` исторически осталась в названии, но в пилоте она используется как дефолтная категория. Если указано `Роза`, приложение может использовать `Роза общ` как дефолтный групповой запрос при наличии такой категории в данных.

## XLS-импорт из 1С

Главный экран содержит блок `Импорт XLS из 1С`:

- `Номенклатура XLS`;
- `Продажи XLS`;
- `Остатки XLS`;
- `Поступления XLS`.

Импортер поддерживает разные выгрузки 1С, включая отчеты, где магазины/склады идут в колонках. Если продажи выгружены без привязки к магазинам, график “Продажи по магазинам” не строится, а интерфейс показывает понятное сообщение.

## CSV-импорт

CSV-эндпоинты оставлены для совместимости:

```text
POST /import/products
POST /import/sales
POST /import/stocks
POST /import/purchases
```

Ожидаемые поля:

`products.csv`

```csv
onec_id,purchase_name,sales_category,flower_type,variety,height_cm,allocation_weight
rose_montblanc_50,Роза Эквадор Монблан 50,Роза,rose,Монблан,50,1
```

`sales.csv`

```csv
date,store_id,store_name,product_id_1c,product_name,category_name,quantity,revenue,sale_type
15.03.2025,store_1,Магазин 1,rose_montblanc_50,Роза Эквадор Монблан 50,Роза,30,4500,retail
```

`stocks.csv`

```csv
date,store_id,store_name,product_id_1c,product_name,category_name,quantity
01.03.2026,store_1,Магазин 1,rose_montblanc_50,Роза Эквадор Монблан 50,Роза,80
```

`purchases.csv`

```csv
order_date,delivery_date,product_id_1c,product_name,category_name,quantity_ordered,quantity_received,supplier
01.03.2026,22.03.2026,rose_montblanc_50,Роза Эквадор Монблан 50,Роза,300,300,Поставщик
```

## Тесты

Запуск:

```bash
python -m pytest tests
```

Через Docker:

```bash
docker compose run --rm flower-forecast pytest -q tests
```

## Безопасность

Сейчас на сервере уже сделано:

- приложение доступно только через nginx;
- прямой порт приложения закрыт;
- включен UFW;
- `.env` не хранится в репозитории;
- `APP_DEBUG=false` в production;
- есть backup базы;
- включена авторизация FastAPI через cookie-сессию;
- пароль администратора хранится в SQLite в виде хэша;
- опасное действие `Очистить базу` требует отдельного подтверждения в интерфейсе.

## Админ-панель

Админ-панель доступна по адресу:

```text
/admin
```

Состав:

- `/login`;
- `/logout`;
- cookie-сессия на заданное число дней;
- смена пароля с проверкой старого пароля и повтором нового;
- список backup-файлов;
- восстановление backup;
- кнопка ручного backup;
- лог действий администратора.

Первый пользователь создается автоматически при старте, если включено:

```env
ADMIN_AUTH_ENABLED=true
ADMIN_USERNAME=admin
ADMIN_INITIAL_PASSWORD=...
```

После первого входа временный пароль нужно заменить в админ-панели. Для восстановления базы приложение сначала делает свежий backup текущей базы, а потом подменяет SQLite-файл выбранной копией.

## Данные, которые нужны от заказчика

- актуальная номенклатура;
- продажи за доступный период;
- остатки за доступный период;
- поступления/движения товара;
- указание, какие позиции считать группами;
- правила минимальной партии и округления;
- желаемые права доступа пользователей;
- регламент обновления выгрузок;
- список праздников/локальных событий, которые бизнес считает важными.
