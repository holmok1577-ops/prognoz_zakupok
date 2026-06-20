from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Flower Purchase Forecast"
    app_env: str = "local"
    app_debug: bool = True
    app_secret_key: str = "change_me"

    database_url: str = "sqlite:///./data/app.db"

    onec_connection_type: str = "csv"
    onec_base_url: str = ""
    onec_username: str = ""
    onec_password: str = ""
    onec_timeout_seconds: int = 60
    onec_products_path: str = ""
    onec_sales_path: str = ""
    onec_stocks_path: str = ""
    onec_purchases_path: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_enabled: bool = False
    openai_base_url: str = ""

    search_provider: str = "none"
    tavily_api_key: str = ""
    serpapi_api_key: str = ""

    default_region: str = "RU-BU"
    default_city: str = "Ulan-Ude"
    default_timezone: str = "Asia/Irkutsk"

    mvp_product_category: str = Field(default="Роза 50")
    forecast_lead_days: int = 21
    forecast_period_days: int = 7
    trend_lookback_weeks: int = 16
    safety_stock_percent: float = 5.0
    usable_stock_percent: float = 25.0
    stock_shelf_life_days: int = 7
    rose_shelf_life_days: int = 7
    carnation_shelf_life_days: int = 14
    chrysanthemum_shelf_life_days: int = 14

    admin_auth_enabled: bool = False
    admin_username: str = "admin"
    admin_initial_password: str = ""
    admin_session_days: int = 7
    admin_backup_dir: str = "./data/backups"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
