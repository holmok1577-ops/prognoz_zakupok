from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    onec_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    purchase_name: Mapped[str] = mapped_column(String(255), index=True)
    sales_category: Mapped[str] = mapped_column(String(255), index=True)
    flower_type: Mapped[str] = mapped_column(String(80), default="")
    variety: Mapped[str] = mapped_column(String(120), default="")
    height_cm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    allocation_weight: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(default=True)

    sales: Mapped[List["Sale"]] = relationship(back_populates="product")
    stocks: Mapped[List["Stock"]] = relationship(back_populates="product")
    purchases: Mapped[List["PurchaseOrder"]] = relationship(back_populates="product")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    onec_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("sale_date", "store_id", "product_id", "source_row_hash", name="uq_sale_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_date: Mapped[date] = mapped_column(Date, index=True)
    store_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stores.id"), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    sale_type: Mapped[str] = mapped_column(String(32), default="unknown")
    source_row_hash: Mapped[str] = mapped_column(String(64), index=True)

    product: Mapped[Product] = relationship(back_populates="sales")


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint("stock_date", "store_id", "product_id", name="uq_stock_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_date: Mapped[date] = mapped_column(Date, index=True)
    store_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stores.id"), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)

    product: Mapped[Product] = relationship(back_populates="stocks")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("order_date", "delivery_date", "product_id", "source_row_hash", name="uq_purchase_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    delivery_date: Mapped[date] = mapped_column(Date, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity_ordered: Mapped[float] = mapped_column(Float)
    quantity_received: Mapped[float] = mapped_column(Float, default=0.0)
    supplier: Mapped[str] = mapped_column(String(255), default="")
    source_row_hash: Mapped[str] = mapped_column(String(64), index=True)

    product: Mapped[Product] = relationship(back_populates="purchases")


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    target_start_date: Mapped[date] = mapped_column(Date, index=True)
    target_end_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(255), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    items: Mapped[List["RecommendationItem"]] = relationship(back_populates="run")


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("recommendation_runs.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    statistical_quantity: Mapped[float] = mapped_column(Float)
    ai_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    final_quantity: Mapped[float] = mapped_column(Float)
    current_stock: Mapped[float] = mapped_column(Float)
    usable_stock: Mapped[float] = mapped_column(Float, default=0.0)
    stock_snapshot_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    stock_age_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    historical_sold: Mapped[float] = mapped_column(Float, default=0.0)
    historical_leftover: Mapped[float] = mapped_column(Float, default=0.0)
    historical_purchased: Mapped[float] = mapped_column(Float, default=0.0)
    historical_purchase_need: Mapped[float] = mapped_column(Float, default=0.0)
    incoming_orders: Mapped[float] = mapped_column(Float)
    expected_next_receipt_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_next_receipt_note: Mapped[str] = mapped_column(String(255), default="")
    baseline_demand: Mapped[float] = mapped_column(Float)
    trend_coefficient: Mapped[float] = mapped_column(Float)
    trend_current_sales: Mapped[float] = mapped_column(Float, default=0.0)
    trend_previous_sales: Mapped[float] = mapped_column(Float, default=0.0)
    trend_current_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    trend_current_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    trend_previous_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    trend_previous_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    short_history_first_sale_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    short_history_days: Mapped[int] = mapped_column(Integer, default=0)
    short_history_last_7_sales: Mapped[float] = mapped_column(Float, default=0.0)
    short_history_last_30_sales: Mapped[float] = mapped_column(Float, default=0.0)
    short_history_weekly_average: Mapped[float] = mapped_column(Float, default=0.0)
    short_history_monthly_average: Mapped[float] = mapped_column(Float, default=0.0)
    short_history_period_average: Mapped[float] = mapped_column(Float, default=0.0)
    safety_stock: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[RecommendationRun] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    user: Mapped[AdminUser] = relationship()


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    username: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(120))
    details: Mapped[str] = mapped_column(Text, default="")
