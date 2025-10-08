# app/api/utils.py
from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, Tuple
from fastapi import Request
from uuid import uuid4
import os
import re

BASE_DIR = Path(__file__).resolve().parents[1]  # app/
UPLOAD_DIR = BASE_DIR / "uploads"

# Публичный базовый URL сервера (для формирования абсолютных ссылок)
PUBLIC_BACKEND_URL = os.getenv(
    "PUBLIC_BACKEND_URL",
    "https://cautious-space-carnival-9g9996wrj7p2p7q6-8000.app.github.dev",
)


def abs_url(request: Request, maybe_url: Optional[str]) -> Optional[str]:
    """
    Делает абсолютный URL. Если maybe_url уже абсолютный — вернёт как есть.
    Иначе — приклеит либо PUBLIC_BACKEND_URL, либо origin из request.base_url.
    """
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


# =========================== Файловые утилиты ===========================

_WIN_FORBIDDEN = r'\\/:*?"<>|'
_CTRL_CHARS = r"[\u0000-\u001F\u007F]"


def sanitize_filename_unicode(
    name: Optional[str],
    *,
    default_name: str = "document",
    default_ext: Optional[str] = None,
    allowed_ext: Optional[set[str]] = None,
    max_len: int = 180,
) -> str:
    """
    Нормализует имя файла, сохраняя кириллицу/Unicode.
    - вырезает управляющие и заведомо проблемные FS-символы (Windows/URL)
    - схлопывает пробелы, режет по длине
    - гарантирует расширение (если передан default_ext)
    - при allowed_ext — если расширение не из списка, принудительно ставит default_ext
    """
    base = (name or default_name).strip()
    # Нормализуем Unicode-комбинации
    try:
        base = base.encode("utf-8", "ignore").decode("utf-8")
        base = base.replace("\u200b", "")  # zero-width space
        base = base.replace("\ufeff", "")  # BOM
        base = base.replace("\u2060", "")  # word joiner
    except Exception:
        pass

    # Убираем управляющие и запрещённые для путей символы
    base = re.sub(_CTRL_CHARS, "", base)
    base = re.sub(f"[{re.escape(_WIN_FORBIDDEN)}]+", "", base)

    # Схлопываем пробелы
    base = re.sub(r"\s+", " ", base).strip()

    if not base:
        base = default_name

    # Выделяем расширение
    stem = base
    ext = ""
    if "." in base:
        stem, ext = base.rsplit(".", 1)
        ext = ext.lower()

    # Разруливаем расширения
    if allowed_ext is not None:
        if ext not in allowed_ext:
            ext = (default_ext or "").lstrip(".").lower()
    else:
        if not ext and default_ext:
            ext = default_ext.lstrip(".").lower()

    # Ограничение длины (учитывая точку и расширение)
    if max_len and len(stem) > max_len:
        stem = stem[:max_len]

    if ext:
        return f"{stem}.{ext}"
    return stem


def ensure_unique_path(path: Path) -> Path:
    """
    Если path уже существует — добавляет -1, -2, ... перед расширением, пока не станет уникальным.
    """
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        cand = parent / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def save_binary_to_uploads(
    data: bytes,
    *,
    subdir: str,
    filename: Optional[str] = None,
    default_name: str = "file",
    default_ext: Optional[str] = None,
    allowed_ext: Optional[set[str]] = None,
) -> Tuple[Path, str]:
    """
    Сохраняет бинарные данные в uploads/<subdir>/<filename>.
    Возвращает (abs_path, rel_url).

    rel_url всегда вида: /files/<subdir>/<filename>
    """
    safe_name = sanitize_filename_unicode(
        filename,
        default_name=default_name,
        default_ext=default_ext,
        allowed_ext=allowed_ext,
    )
    target_dir = UPLOAD_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    abs_path = ensure_unique_path(target_dir / safe_name)
    abs_path.write_bytes(data)

    rel_url = f"/files/{subdir}/{abs_path.name}"
    return abs_path, rel_url


def save_html_to_uploads(
    html: str,
    request: Request,
    *,
    filename: Optional[str] = None,
    stem: Optional[str] = None,
) -> str:
    """
    Сохраняет HTML в uploads/htmls/<filename>.html и возвращает АБСОЛЮТНЫЙ URL
    по /files/htmls/<filename>.html (через abs_url).

    Если передать filename — он будет нормализован и сохранён (кириллица сохраняется).
    Если передать stem — имя будет <stem>.html.
    Если ничего не передано — используется uuid.
    """
    if filename:
        safe_name = sanitize_filename_unicode(
            filename,
            default_name="document",
            default_ext="html",
            allowed_ext={"html"},
        )
    elif stem:
        safe_name = sanitize_filename_unicode(
            f"{stem}.html",
            default_name="document",
            default_ext="html",
            allowed_ext={"html"},
        )
    else:
        safe_name = f"{uuid4().hex}.html"

    htmls_dir = UPLOAD_DIR / "htmls"
    htmls_dir.mkdir(parents=True, exist_ok=True)

    abs_path = ensure_unique_path(htmls_dir / safe_name)
    abs_path.write_text(html, encoding="utf-8")

    rel_url = f"/files/htmls/{abs_path.name}"
    return abs_url(request, rel_url)
