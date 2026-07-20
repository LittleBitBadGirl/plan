from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String

from app.models.base import Base


class InvestmentSnapshot(Base):
    __tablename__ = "investment_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    date = Column(Date, nullable=False, index=True)
    total_balance = Column(Float, nullable=False)


class InvestmentFlow(Base):
    __tablename__ = "investment_flows"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, nullable=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True, index=True)
    date = Column(Date, nullable=False, index=True)
    type = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String(500), nullable=True)
