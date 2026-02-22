from fastapi import APIRouter
from app.config import API_VERSION

router = APIRouter()


@router.get("/health", tags=["Meta"])
def health_check():
    """Simple uptime check required by RapidAPI."""
    return {"status": "ok", "version": API_VERSION}
