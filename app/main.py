from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import API_VERSION
from app.middleware import RequestLoggingMiddleware
from app.routers import health, inspect, fill

app = FastAPI(
    title="PDF Form Auto-Fill API",
    description=(
        "Fill AcroForm PDF fields via REST. "
        "Use **/inspect** to discover field names, then **/fill** to fill them."
    ),
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(RequestLoggingMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(inspect.router)
app.include_router(fill.router)


# ── Exception Handlers ─────────────────────────────────────────────────────────

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle our custom errors and FastAPI built-in HTTP errors."""
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP_ERROR", "message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """Handle FastAPI validation errors (e.g. missing fields) cleanly."""
    return JSONResponse(
        status_code=422,
        content={"error": "VALIDATION_ERROR", "message": str(exc)},
    )


import traceback

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Catch-all for unexpected internal errors."""
    # Print the traceback to the server logs for debugging
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred."},
    )
