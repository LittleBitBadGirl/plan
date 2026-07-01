from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.models.base import Base


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String(500), nullable=False)
    ocr_status = Column(String(20), default="pending")  # pending/success/failed
    ocr_result = Column(String, nullable=True)  # JSON
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
