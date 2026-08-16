from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import REPOSITORY_ROOT, get_settings
from backend.app.database import init_db
from backend.app.routes import router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment not in {"development", "test"} and not settings.admin_token:
        raise RuntimeError("ADMIN_TOKEN must be configured outside development and test")
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="3.0.0",
    description="酒店空间建库、环境初评、证据深审和房间检查 API。",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token", "X-User-ID"],
)
app.include_router(router)
app.mount("/data", StaticFiles(directory=str(REPOSITORY_ROOT / "data")), name="data")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    return FileResponse(REPOSITORY_ROOT / "index.html")


@app.get("/runtime-config.js", include_in_schema=False, response_class=PlainTextResponse)
def runtime_config() -> str:
    return "window.KELI_STATIC_SNAPSHOT = false;"
