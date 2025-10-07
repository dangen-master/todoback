from pathlib import Path
import os
from urllib.parse import urljoin, urlparse
from typing import Optional
from fastapi import Request
from uuid import uuid4 

BASE_DIR = Path(__file__).resolve().parents[1]  # app/
UPLOAD_DIR = BASE_DIR / "uploads"

PUBLIC_BACKEND_URL = os.getenv(
    "PUBLIC_BACKEND_URL",
    "https://cautious-space-carnival-9g9996wrj7p2p7q6-8000.app.github.dev",
)

def abs_url(request: Request, maybe_url: Optional[str]) -> Optional[str]:
    if not maybe_url:
        return None
    try:
        parsed = urlparse(maybe_url)
        if parsed.scheme:
            return maybe_url
    except Exception:
        pass

    base = (PUBLIC_BACKEND_URL or "").strip()
    if base:
        base = base.rstrip("/") + "/"
        return urljoin(base, maybe_url.lstrip("/"))

    origin = str(request.base_url).rstrip("/") + "/"
    return urljoin(origin, maybe_url.lstrip("/"))



def save_html_to_uploads(html: str, request: Request) -> str:
    """
    Сохраняет HTML в uploads/htmls/<uuid>.html и возвращает абсолютный URL
    по /files/htmls/<uuid>.html (через abs_url).
    """
    htmls_dir = UPLOAD_DIR / "htmls"
    htmls_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid4().hex}.html"
    fpath = htmls_dir / fname
    fpath.write_text(html, encoding="utf-8")
    rel_url = f"/files/htmls/{fname}"
    return abs_url(request, rel_url)
