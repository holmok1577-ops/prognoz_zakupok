# Flower Purchase Forecast

MVP FastAPI-приложения для прогноза закупки цветов. Текущая версия сфокусирована на одной категории продаж, например `Роза 50`, чтобы проверить качество прогноза на старых данных и сравнить рекомендацию с реальными закупками.

## Что уже заложено

- импорт CSV из 1С: номенклатура, продажи, остатки, реальные закупки;
- автоматическая загрузка из 1С по HTTP/OData JSON-контракту;
- статистический прогноз по аналогичному периоду прошлого года;
- коэффициент тренда по последним неделям;
- учет текущих остатков и ожидаемых поставок;
- сохранение расчетов;
- сравнение рекомендации с фактической закупкой;
- первичная AI-рекомендация через OpenAI поверх рассчитанной статистики;
- опциональная AI-рекомендация с учетом календаря событий и праздников;
- Docker/Docker Compose для деплоя.

## Важное про интернет и OpenAI

`gpt-4.1-mini` не ходит в интернет сам по себе. Для интернет-факторов нужно подключить отдельный источник: Tavily, SerpAPI, Google Search API или собственный календарь событий. В MVP используется локальный календарь событий из `app/prompts/flower_demand_events.md` и `app/services/event_calendar.py`.

Даже первичная рекомендация может проходить через модель: приложение считает сухую статистическую базу, а модель превращает ее в понятный текст вроде: “Рекомендую закупить 100 роз, потому что в аналогичном периоде спрос был 120, текущий остаток 20, ожидаемых поставок нет”. Числа передаются модели из приложения, модель не должна придумывать продажи.

## Быстрый старт локально

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Откройте `http://127.0.0.1:8000`.

## Запуск через Docker

```bash
copy .env.example .env
docker compose up --build
```

Приложение будет доступно на `http://127.0.0.1:8000`.

Проверка тестов в том же Docker-окружении:

```bash
docker compose run --rm flower-forecast pytest -q
```

В `docker-compose.yml` выставлены:

- `mem_limit: 512m`;
- `memswap_limit: 768m`;
- `cpus: 1.0`;
- один `uvicorn` worker;
- `--limit-concurrency 64`;
- `MALLOC_ARENA_MAX=2`;
- healthcheck.

Для MVP это снижает риск падения контейнера из-за разрастания памяти. При больших выгрузках лучше перейти на PostgreSQL и пакетную обработку импорта.

## Что внести в `.env`

Минимально для MVP:

```env
APP_SECRET_KEY=change_me
DATABASE_URL=sqlite:///./data/app.db
MVP_PRODUCT_CATEGORY="Роза 50"
FORECAST_LEAD_DAYS=21
FORECAST_PERIOD_DAYS=7
TREND_LOOKBACK_WEEKS=8
SAFETY_STOCK_PERCENT=5
USABLE_STOCK_PERCENT=25
STOCK_SHELF_LIFE_DAYS=7
```

Для OpenAI:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_ENABLED=true
```

Для будущего прямого подключения к 1С:

```env
ONEC_CONNECTION_TYPE=odata
ONEC_BASE_URL=https://your-1c-host/odata/standard.odata
ONEC_USERNAME=readonly_user
ONEC_PASSWORD=readonly_password
ONEC_TIMEOUT_SECONDS=60
ONEC_PRODUCTS_PATH=products
ONEC_SALES_PATH=sales
ONEC_STOCKS_PATH=stocks
ONEC_PURCHASES_PATH=purchases
```

Кнопка `Загрузить из 1С` вызывает эти адреса и ожидает JSON-массивы или OData-ответы вида `{ "value": [...] }`. Поля внутри должны совпадать с CSV-форматами ниже.

Для будущего интернет-поиска:

```env
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=...
```

## CSV-форматы

Все CSV лучше сохранять в UTF-8 with BOM или UTF-8.

Готовые тестовые файлы лежат в папке `sample_data/`:

```text
sample_data/products.csv
sample_data/sales.csv
sample_data/stocks.csv
sample_data/purchases.csv
```

Их можно загрузить через интерфейс в таком порядке: номенклатура, продажи, остатки, реальные закупки. После этого сформируйте прогноз для категории `Роза 50` на период `18.06.2026` - `24.06.2026`.

Тестовый набор можно пересоздать командой:

```bash
python tools/generate_sample_data.py
```

Состав тестовых данных:

- `products.csv`: 1 закупочная позиция;
- `sales.csv`: 1161 строка продаж по 9 магазинам, включая историю 2025/2026 и пиковые цветочные даты;
- `stocks.csv`: остатки по 9 магазинам на `17.06.2026`, всего 20 шт.;
- `purchases.csv`: исторические и фактические закупки, включая факт 100 шт. для периода `18.06.2026` - `24.06.2026`.

Для скоропортящихся цветов страховой запас в MVP снижен до `5%`. При горизонте поставки `FORECAST_LEAD_DAYS=21` текущий остаток не уменьшает будущую закупку вообще: розы не доживут до периода поставки. Остаток показывается как информационный риск. Вычитать свежий остаток можно только для короткого горизонта, когда `FORECAST_LEAD_DAYS <= STOCK_SHELF_LIFE_DAYS`.

`products.csv`

```csv
onec_id,purchase_name,sales_category,flower_type,variety,height_cm,allocation_weight
rose_montblanc_50,Роза Эквадор Монблан 50,Роза 50,rose,Монблан,50,1
```

`sales.csv`

```csv
date,store_id,store_name,product_id_1c,product_name,category_name,quantity,revenue,sale_type
15.03.2025,store_1,Магазин 1,rose_montblanc_50,Роза Эквадор Монблан 50,Роза 50,30,4500,retail
```

`stocks.csv`

```csv
date,store_id,store_name,product_id_1c,product_name,category_name,quantity
01.03.2026,store_1,Магазин 1,rose_montblanc_50,Роза Эквадор Монблан 50,Роза 50,80
```

`purchases.csv`

```csv
order_date,delivery_date,product_id_1c,product_name,category_name,quantity_ordered,quantity_received,supplier
01.03.2026,22.03.2026,rose_montblanc_50,Роза Эквадор Монблан 50,Роза 50,300,300,Поставщик
```

## Данные, которые нужно получить от заказчика

- тестовые выгрузки продаж минимум за 2 года;
- остатки на даты перед прогнозируемыми периодами;
- реальные закупки за те же периоды;
- справочник номенклатуры;
- маппинг закупочных сортов в продаваемые категории;
- правила минимальной партии и округления;
- подтверждение, как отделять опт от розницы;
- доступ к 1С API/OData или регламент CSV-выгрузки;
- OpenAI API key;
- ключ поискового API, если нужна настоящая интернет-проверка праздников и событий.
