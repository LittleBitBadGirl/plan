from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(50), nullable=False, unique=True, index=True)
    type = Column(String(20), nullable=False)  # iis, broker, reserve
    legacy_goal_id = Column(Integer, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    broker_contract = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    goals = relationship("PortfolioGoal", back_populates="portfolio")
    positions = relationship("Position", back_populates="portfolio")
    import_logs = relationship("ImportLog", back_populates="portfolio")


class PortfolioGoal(Base):
    __tablename__ = "portfolio_goals"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    portfolio = relationship("Portfolio", back_populates="goals")


class Instrument(Base):
    __tablename__ = "instruments"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(20), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    asset_type = Column(String(20), nullable=False, default="other")
    maturity_date = Column(Date, nullable=True)
    coupon_rate = Column(Float, nullable=True)
    aliases = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    positions = relationship("Position", back_populates="instrument")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "instrument_id",
            "snapshot_date",
            name="uq_position_snapshot",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    quantity = Column(Float, nullable=False)
    avg_price = Column(Float, nullable=True)
    market_value = Column(Float, nullable=False)
    weight_pct = Column(Float, nullable=True)

    portfolio = relationship("Portfolio", back_populates="positions")
    instrument = relationship("Instrument", back_populates="positions")


class ImportLog(Base):
    __tablename__ = "import_log"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False)
    source = Column(String(50), nullable=False, default="hermes")
    payload_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="ok")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    portfolio = relationship("Portfolio", back_populates="import_logs")
