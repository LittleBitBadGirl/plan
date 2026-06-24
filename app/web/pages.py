"""Web UI router assembly — domain routes live in app/web/routes/."""
from fastapi import APIRouter

from app.web.routes import (
    archive,
    backlog,
    calendar,
    career,
    categories,
    dashboard,
    finance,
    reading,
    recurring,
    shopping,
    stats,
    tasks,
)

router = APIRouter(tags=["web"])

router.include_router(dashboard.router)
router.include_router(tasks.router)
router.include_router(backlog.router)
router.include_router(categories.router)
router.include_router(calendar.router)
router.include_router(archive.router)
router.include_router(stats.router)
router.include_router(recurring.router)
router.include_router(shopping.router)
router.include_router(reading.router)
router.include_router(career.router)
router.include_router(finance.router)

# Re-export for backward compatibility (e.g. app.api.recurring)
from app.web.deps import get_today_stats  # noqa: F401

__all__ = ["router", "get_today_stats"]
