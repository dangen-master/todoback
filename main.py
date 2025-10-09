# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from models import init_db
from api.utils import UPLOAD_DIR   # ← единый источник пути uploads/
from api.routers.health import router as health_router
from api.routers.users import router as users_router
from api.routers.roles import router as roles_router
from api.routers.groups import router as groups_router
from api.routers.subjects import router as subjects_router
from api.routers.lessons import router as lessons_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Edu MiniApp API", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://telegrammapp-44890.web.app",
        "https://*.app.github.dev",
    ],
    allow_origin_regex=r"https://.*\.app\.github\.dev",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── создаём все подпапки один раз при старте ─────────────────────────
(UPLOAD_DIR / "pdfs").mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "htmls").mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "logs").mkdir(parents=True, exist_ok=True)

# можно полезно подсказать в stderr, куда именно смонтировали
print(f"[startup] UPLOAD_DIR={UPLOAD_DIR.resolve()}", flush=True)

# статика отдаётся из того же UPLOAD_DIR
app.mount("/files", StaticFiles(directory=str(UPLOAD_DIR), html=False), name="files")

# routers
app.include_router(health_router)
app.include_router(users_router,  prefix="/api")
app.include_router(roles_router,   prefix="/api")
app.include_router(groups_router,  prefix="/api")
app.include_router(subjects_router, prefix="/api")
app.include_router(lessons_router,  prefix="/api")
