from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter()


@router.get("/")
async def root(settings: Settings = Depends(get_settings)):
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }


@router.get("/health")
async def health_check(settings: Settings = Depends(get_settings)):
    return {
        "status": "healthy",
        "environment": settings.environment,
    }
