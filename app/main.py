from fastapi import FastAPI
from sqlalchemy import text
from starlette.middleware.sessions import (
    SessionMiddleware,
)

from app.config import settings
from app.database import engine
from app.redis_client import redis_client
from app.routes.panel import router as panel_router
from app.routes.webhooks import (
    router as webhooks_router,
)


app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.admin_session_secret,
    session_cookie="whatsapp_lotes_panel",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=True,
)

app.include_router(
    webhooks_router
)

app.include_router(
    panel_router
)


@app.get("/health")
def health() -> dict:
    database_ok = False
    redis_ok = False

    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT 1")
            )

        database_ok = True

    except Exception:
        database_ok = False

    try:
        redis_ok = bool(
            redis_client.ping()
        )

    except Exception:
        redis_ok = False

    status = (
        "ok"
        if database_ok and redis_ok
        else "degraded"
    )

    return {
        "status": status,
        "database": database_ok,
        "redis": redis_ok,
        "app": settings.app_name,
        "environment": settings.app_env,
    }
