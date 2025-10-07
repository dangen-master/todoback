#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from __future__ import annotations
import argparse
import base64
import html as html_lib
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import fitz  # PyMuPDF

HTML_SHELL = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    :root {{ --bg:#fff; --fg:#111; }}
    html,body {{ margin:0; padding:0; background:var(--bg); color:var(--fg); }}
    body {{
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,"Apple Color Emoji","Segoe UI Emoji";
      -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale;
    }}
    .doc {{ display:flex; flex-direction:column; align-items:center; gap:24px; padding:24px 12px 48px; }}
    .page {{ position:relative; box-shadow:0 2px 12px rgba(0,0,0,.12); background:#fff; overflow:hidden; }}
    .text-layer, .image-layer {{ position:absolute; inset:0; transform-origin:top left; }}
    .text-layer {{ z-index:2; pointer-events:none; }}
    .image-layer {{ z-index:1; pointer-events:none; }}
    .t {{ position:absolute; white-space:pre; line-height:1; }}
  </style>
</head>
<body>
  <div class="doc">{body}</div>
</body>
</html>
"""

# --------------------------- helpers ---------------------------

def _int_rgb_to_css(c: int) -> str:
    r = (c >> 16) & 255
    g = (c >> 8) & 255
    b = c & 255
    return f"rgb({r},{g},{b})"

def _detect_bold_italic(font_name: str) -> tuple[bool, bool]:
    name = font_name.lower()
    bold = any(k in name for k in ("bold", "semibold", "demi", "black", "heavy"))
    italic = any(k in name for k in ("italic", "oblique", "ital"))
    return bold, italic

def _sanitize_text(t: str) -> str:
    t = t.replace("\r", "")
    return html_lib.escape(t, quote=False)

def _png_dataurl_from_pixmap(pix: fitz.Pixmap) -> str:
    png = pix.tobytes("png")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

# --------------------------- image extraction ---------------------------

def _pixmap_from_xref_safely(doc: fitz.Document, xref: int, smask_xref: Optional[int], mask_xref: Optional[int]) -> Optional[fitz.Pixmap]:
    """
    Пробуем собрать pixmap изображения из xref:
    - учитываем soft-mask (SMask) и hard-mask (Mask) если возможно;
    - приводим к RGB и сплющиваем альфу на белый фон.
    Возвращаем уже готовый RGB pixmap БЕЗ альфы (на белом).
    """
    try:
        base = fitz.Pixmap(doc, xref)

        # Hard mask (color key) часто даёт чёрный фон. PyMuPDF не всегда легко приклеить,
        # но если есть smask_xref — используем его как альфу.
        if smask_xref and not base.alpha:
            try:
                sm = fitz.Pixmap(doc, smask_xref)
                if sm.n != 1:
                    sm = fitz.Pixmap(fitz.csGRAY, sm)
                base = fitz.Pixmap(base, sm)  # добавит альфу
            except Exception:
                pass

        # Приводим к RGB
        if not (base.colorspace and base.colorspace.n == 3):
            base = fitz.Pixmap(fitz.csRGB, base)

        # Сплющиваем альфу на белый (если осталась)
        if base.alpha:
            base = fitz.Pixmap(fitz.csRGB, base)

        return base
    except Exception:
        return None

def _xref_meta_list(page: fitz.Page) -> List[Dict]:
    """
    Собираем метаданные об изображениях:
    - из page.get_images(full=True): xref, smask, colorspace и т.п.
    - сопоставляем bbox через page.get_image_info(xrefs=True) и rawdict.
    """
    # 1) базовая таблица по xref
    meta: Dict[int, Dict] = {}
    for img in page.get_images(full=True):
        # В новых версиях: (xref, smask, x, y, bpc, colorspace, alt, name, filter, width, height, ...)
        xref = img[0]
        smask = img[1] if len(img) > 1 else 0
        cs = img[5] if len(img) > 5 else None  # строка вида 'DeviceRGB', 'DeviceCMYK', 'ICCBased', ...
        meta[xref] = {"xref": xref, "smask": smask or None, "mask": None, "cs": cs, "bbox": None}

    # 2) bbox из image_info (самый точный)
    try:
        for rec in page.get_image_info(xrefs=True):
            xref = rec.get("xref")
            if isinstance(xref, int):
                meta.setdefault(xref, {"xref": xref, "smask": None, "mask": None, "cs": None, "bbox": None})
                if rec.get("bbox"):
                    meta[xref]["bbox"] = tuple(rec["bbox"])
                # иногда тут есть ключи 'smask' / 'mask'
                if rec.get("smask"):
                    meta[xref]["smask"] = rec.get("smask")
                if rec.get("mask"):
                    meta[xref]["mask"] = rec.get("mask")
    except Exception:
        pass

    # 3) доп. bbox из rawdict
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 1:
            continue
        xref = block.get("image") if isinstance(block.get("image"), int) else block.get("number")
        if isinstance(xref, int):
            meta.setdefault(xref, {"xref": xref, "smask": None, "mask": None, "cs": None, "bbox": None})
            if not meta[xref]["bbox"] and block.get("bbox"):
                meta[xref]["bbox"] = tuple(block["bbox"])

    # Возвращаем список только с валидным bbox
    out: List[Dict] = []
    for x in meta.values():
        if x.get("bbox"):
            out.append(x)
    return out

def _should_clip(meta: Dict, image_mode: str) -> bool:
    """Решаем, нужно ли делать клиповый рендер вместо прямого извлечения."""
    if image_mode == "clip":
        return True
    if image_mode == "extract":
        return False
    # auto-эвристика:
    cs = (meta.get("cs") or "").upper()
    if meta.get("smask") or meta.get("mask"):
        return True
    if "CMYK" in cs or "ICC" in cs:
        return True
    return False

def _render_clip(page: fitz.Page, bbox_pt: Tuple[float,float,float,float], oversample: float = 2.0) -> fitz.Pixmap:
    """
    Растеризуем участок страницы (bbox_pt в pt) на белом фоне.
    oversample > 1.0 даёт более чёткое изображение (пикселей больше),
    браузер потом удачно ужмёт под CSS-размеры.
    """
    rect = fitz.Rect(*bbox_pt)
    mat = fitz.Matrix(oversample, oversample)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)  # alpha=False => белый фон
    # На всякий — приводим к RGB (обычно уже RGB)
    if not (pix.colorspace and pix.colorspace.n == 3) or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix

# --------------------------- page render ---------------------------

def _render_page_html(doc: fitz.Document, page_index: int, scale: float, image_mode: str, clip_oversample: float, debug: bool) -> str:
    page = doc[page_index]
    pw_pt, ph_pt = page.rect.width, page.rect.height
    pw_css = pw_pt * scale
    ph_css = ph_pt * scale

    # Текстовые спаны
    tdict = page.get_text("dict")
    spans_html: List[str] = []
    for block in tdict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                size = span.get("size", 12)
                color = _int_rgb_to_css(span.get("color", 0))
                font = span.get("font", "sans-serif")
                bold, italic = _detect_bold_italic(font)

                styles = [
                    f"left:{x0*scale:.2f}px",
                    f"top:{y0*scale:.2f}px",
                    f"font-size:{size*scale:.3f}px",
                    f"color:{color}",
                    f"font-family:{html_lib.escape(font)}",
                ]
                if bold:
                    styles.append("font-weight:700")
                if italic:
                    styles.append("font-style:italic")

                spans_html.append(f'<div class="t" style="{";".join(styles)}">{_sanitize_text(text)}</div>')

    # Картинки: после текста в DOM
    images_html: List[str] = []
    for meta in _xref_meta_list(page):
        bbox = meta["bbox"]
        x0, y0, x1, y1 = bbox
        width = (x1 - x0) * scale
        height = (y1 - y0) * scale
        if width <= 0 or height <= 0:
            continue

        use_clip = _should_clip(meta, image_mode)
        dataurl: Optional[str] = None

        if not use_clip:
            # аккуратное извлечение
            pix = _pixmap_from_xref_safely(doc, meta["xref"], meta.get("smask"), meta.get("mask"))
            if pix is not None:
                dataurl = _png_dataurl_from_pixmap(pix)
            else:
                use_clip = True

        if use_clip:
            # клиповый рендер участка страницы (на белом фоне) — всегда без «чёрных фонов»
            pix = _render_clip(page, bbox, oversample=clip_oversample)
            dataurl = _png_dataurl_from_pixmap(pix)

        if debug:
            print(f"[img] p{page_index+1} xref={meta.get('xref')} mode={'clip' if use_clip else 'extract'} cs={meta.get('cs')} bbox={bbox} ok={dataurl is not None}")

        if not dataurl:
            continue

        styles = [
            "position:absolute",
            f"left:{x0*scale:.2f}px",
            f"top:{y0*scale:.2f}px",
            f"width:{width:.2f}px",
            f"height:{height:.2f}px",
            "object-fit:contain",
        ]
        images_html.append(f'<img class="img" src="{dataurl}" alt="" style="{";".join(styles)}" />')

    return f"""
    <section class="page" style="width:{pw_css:.2f}px;height:{ph_css:.2f}px">
      <div class="text-layer">{''.join(spans_html)}</div>
      <div class="image-layer">{''.join(images_html)}</div>
    </section>
    """

def pdf_to_html(input_pdf: Path, scale: float = 96/72, image_mode: str = "auto", clip_oversample: float = 2.0, debug: bool = False) -> str:
    assert image_mode in ("auto", "extract", "clip")
    with fitz.open(input_pdf) as doc:
        pages_html: List[str] = []
        for i in range(len(doc)):
            pages_html.append(_render_page_html(doc, i, scale, image_mode, clip_oversample, debug))
        return HTML_SHELL.format(
            title=html_lib.escape(input_pdf.stem),
            body="\n".join(pages_html),
        )

# --------------------------- CLI ---------------------------

def main():
    ap = argparse.ArgumentParser(description="PDF -> HTML (текст сверху, картинки после текста; анти-чёрный фон)")
    ap.add_argument("input", type=Path, help="Входной PDF")
    ap.add_argument("output", type=Path, help="Выходной HTML")
    ap.add_argument("--scale", type=float, default=96/72, help="Масштаб (CSS px на PDF pt), по умолчанию ~1.3333")
    ap.add_argument("--image-mode", choices=["auto", "extract", "clip"], default="auto",
                    help="Способ работы с картинками: auto (дефолт), extract или clip")
    ap.add_argument("--clip-oversample", type=float, default=2.0,
                    help="Коэффициент пересэмплинга при clip-рендере (качество ↑, вес ↑)")
    ap.add_argument("--debug", action="store_true", help="Печатать отладочную информацию")
    args = ap.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Файл не найден: {args.input}")

    html_out = pdf_to_html(args.input, scale=args.scale, image_mode=args.image_mode,
                           clip_oversample=args.clip_oversample, debug=args.debug)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_out, encoding="utf-8")
    print(f"Готово: {args.output}")

if __name__ == "__main__":
    main()
