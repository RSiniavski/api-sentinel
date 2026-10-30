
from fastapi import FastAPI

from app.config import get_settings
from app.api.routes.health import router as health_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Intelligent API Testing & Monitoring",
    version=settings.app_version,
)

app.include_router(health_router)
