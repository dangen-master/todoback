# tests/conftest.py
import os
import sys
import pathlib
import pytest
from httpx import AsyncClient, ASGITransport

# --- путь к корню проекта, чтобы `from main import app` работало ---
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- БД по умолчанию: db.sqlite3 в корне проекта ---
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{ROOT / 'db.sqlite3'}")

# --- anyio: фиксируем один backend, чтобы не требовался trio ---
@pytest.fixture
def anyio_backend():
    return "asyncio"

# --- общий HTTP-клиент для тестов (ASGITransport вместо app=) ---
@pytest.fixture
async def client():
    from main import app  # импорт после фикса sys.path
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
