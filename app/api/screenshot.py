import json
import asyncio
import shutil
from pathlib import Path
from datetime import date

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.dependencies import get_db_session
from app.services.ocr_service import ocr_service
from app.models.screenshot import Screenshot
from app.models.task import Task
from app.config import settings

router = APIRouter(prefix="/api/screenshot", tags=["screenshot"])


@router.post("")
async def upload_screenshot(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Загрузить скриншот календаря"""
    # Сохранить файл
    uploads_dir = settings.uploads_dir / "screenshots"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    file_path = uploads_dir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Создать запись скриншота
    screenshot = Screenshot(
        file_path=str(file_path),
        ocr_status="pending",
    )
    db.add(screenshot)
    await db.flush()
    await db.refresh(screenshot)

    # Обработать в фоне (OCR → задачи)
    asyncio.create_task(_process_screenshot(screenshot.id, str(file_path)))

    return {
        "id": screenshot.id,
        "message": "Скриншот загружен, обработка в фоне...",
    }


async def _process_screenshot(screenshot_id: int, file_path: str):
    """Обработать скриншот (OCR + создание задач)"""
    from app.db.database import async_session

    async with async_session() as db:
        # OCR
        result = await ocr_service.process_screenshot(file_path)

        # Обновить скриншот
        sc_result = await db.execute(
            select(Screenshot).where(Screenshot.id == screenshot_id)
        )
        screenshot = sc_result.scalar_one_or_none()
        if not screenshot:
            return

        screenshot.ocr_status = "success" if result.get("events") else "failed"
        screenshot.ocr_result = json.dumps(result)
        await db.flush()

        # Создать задачи из событий
        today = date.today()
        for event in result.get("events", []):
            task = Task(
                title=event["title"],
                description=f"Время: {event['time']}" + (f"-{event['end_time']}" if event.get('end_time') else ""),
                due_date=today,
                source="screenshot",
            )
            db.add(task)

        await db.commit()
