import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("pdf_filler")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with timing and key metadata."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000)

        # Pull metadata set by route handlers (optional — won't fail if absent)
        pdf_source = request.state.__dict__.get("pdf_source", "-")
        fields_count = request.state.__dict__.get("fields_count", "-")

        logger.info(
            "path=%s  method=%s  status=%s  pdf_source=%s  fields=%s  duration_ms=%d",
            request.url.path,
            request.method,
            response.status_code,
            pdf_source,
            fields_count,
            duration_ms,
        )
        return response
