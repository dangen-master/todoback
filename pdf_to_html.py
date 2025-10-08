# pdf_to_html.py
from __future__ import annotations

import base64
import html as html_lib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


# --------------------------- small utils ---------------------------

def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)  # атомарная замена

def _int_rgb_to_css(c: int) -> str:
    r = (c >> 16) & 255
    g = (c >> 8) & 255
    b = c & 255
    return f"rgb({r},{g},{b})"

def _detect_bold_italic(font_name: str) -> tuple[bool, bool]:
    name = (font_name or "").lower()
    bold = any(k in name for k in ("bold", "semibold", "demi", "black", "heavy"))
    italic = any(k in name for k in ("italic", "oblique", "ital"))
    return bold, italic

def _sanitize_text(t: str) -> str:
    t = (t or "").replace("\r", "")
    return html_lib.escape(t, quote=False)

def _png_dataurl_from_pixmap(pix: fitz.Pixmap) -> str:
    return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii")


# --------------------------- image helpers ---------------------------

def _pixmap_from_xref_safely(
    doc: fitz.Document, xref: int,
    smask_xref: Optional[int], mask_xref: Optional[int]
) -> Optional[fitz.Pixmap]:
    """
    Создаём Pixmap из xref, учитывая SMask/Mask, приводим к RGB и сплющиваем альфу на белый.
    Возвращаем RGB без альфы.
    """
    try:
        base = fitz.Pixmap(doc, xref)

        if smask_xref and not base.alpha:
            try:
                sm = fitz.Pixmap(doc, smask_xref)
                if sm.n != 1:
                    sm = fitz.Pixmap(fitz.csGRAY, sm)
                base = fitz.Pixmap(base, sm)
            except Exception:
                pass

        if not (base.colorspace and base.colorspace.n == 3):
            base = fitz.Pixmap(fitz.csRGB, base)

        if base.alpha:
            base = fitz.Pixmap(fitz.csRGB, base)

        return base
    except Exception:
        return None

def _xref_meta_list(page: fitz.Page) -> List[Dict]:
    """
    Собираем изображения с bbox: комбинируем get_images/full, get_image_info и rawdict.
    """
    meta: Dict[int, Dict] = {}

    for img in page.get_images(full=True):
        xref = img[0]
        smask = img[1] if len(img) > 1 else 0
        cs = img[5] if len(img) > 5 else None
        meta[xref] = {"xref": xref, "smask": smask or None, "mask": None, "cs": cs, "bbox": None}

    try:
        for rec in page.get_image_info(xrefs=True):
            xref = rec.get("xref")
            if not isinstance(xref, int):
                continue
            m = meta.setdefault(xref, {"xref": xref, "smask": None, "mask": None, "cs": None, "bbox": None})
            if rec.get("bbox"):
                m["bbox"] = tuple(rec["bbox"])
            if rec.get("smask"):
                m["smask"] = rec.get("smask")
            if rec.get("mask"):
                m["mask"] = rec.get("mask")
    except Exception:
        pass

    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 1:
            continue
        xref = block.get("image") if isinstance(block.get("image"), int) else block.get("number")
        if isinstance(xref, int):
            m = meta.setdefault(xref, {"xref": xref, "smask": None, "mask": None, "cs": None, "bbox": None})
            if not m["bbox"] and block.get("bbox"):
                m["bbox"] = tuple(block["bbox"])

    return [x for x in meta.values() if x.get("bbox")]

def _should_clip(meta: Dict, image_mode: str) -> bool:
    if image_mode == "clip":
        return True
    if image_mode == "extract":
        return False
    cs = (meta.get("cs") or "").upper()
    if meta.get("smask") or meta.get("mask"):
        return True
    if "CMYK" in cs or "ICC" in cs:
        return True
    return False

def _render_clip(page: fitz.Page, bbox_pt: Tuple[float, float, float, float], oversample: float = 2.0) -> fitz.Pixmap:
    rect = fitz.Rect(*bbox_pt)
    mat = fitz.Matrix(oversample, oversample)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
    if not (pix.colorspace and pix.colorspace.n == 3) or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix


# --------------------------- page render ---------------------------

def _render_page_html(
    doc: fitz.Document,
    page_index: int,
    scale: float,
    image_mode: str,
    clip_oversample: float,
    debug: bool
) -> str:
    page = doc[page_index]
    pw_pt, ph_pt = page.rect.width, page.rect.height
    pw_css = pw_pt * scale
    ph_css = ph_pt * scale

    # текст
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

                spans_html.append(
                    f'<div class="t" style="{";".join(styles)}">{_sanitize_text(text)}</div>'
                )

    # изображения
    images_html: List[str] = []
    for meta in _xref_meta_list(page):
        x0, y0, x1, y1 = meta["bbox"]
        width = (x1 - x0) * scale
        height = (y1 - y0) * scale
        if width <= 0 or height <= 0:
            continue

        use_clip = _should_clip(meta, image_mode)
        dataurl: Optional[str] = None

        if not use_clip:
            pix = _pixmap_from_xref_safely(doc, meta["xref"], meta.get("smask"), meta.get("mask"))
            if pix is not None:
                dataurl = _png_dataurl_from_pixmap(pix)
            else:
                use_clip = True

        if use_clip:
            pix = _render_clip(page, meta["bbox"], oversample=clip_oversample)
            dataurl = _png_dataurl_from_pixmap(pix)

        if debug:
            print(f"[img] p{page_index+1} xref={meta.get('xref')} mode={'clip' if use_clip else 'extract'} "
                  f"cs={meta.get('cs')} bbox={meta['bbox']} ok={bool(dataurl)}")

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

    return (
        f'<section class="page" style="width:{pw_css:.2f}px;height:{ph_css:.2f}px">'
        f'<div class="text-layer">{"".join(spans_html)}</div>'
        f'<div class="image-layer">{"".join(images_html)}</div>'
        f'</section>'
    )


# --------------------------- PUBLIC API ---------------------------

def pdf_to_html(
    input_pdf: Path | str,
    output_html: Path | str,
    *,
    scale: float = 96 / 72,
    image_mode: str = "auto",      # "auto" | "extract" | "clip"
    clip_oversample: float = 2.0,
    debug: bool = False,
) -> None:
    """
    Конвертирует PDF → HTML и СРАЗУ записывает в output_html (атомарно).
    Никаких CLI-вызовов, только прямая функция.
    """
    pdf_path = Path(input_pdf)
    html_path = Path(output_html)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    assert image_mode in ("auto", "extract", "clip")

    with fitz.open(pdf_path) as doc:
        pages_html = [
            _render_page_html(doc, i, scale, image_mode, clip_oversample, debug)
            for i in range(len(doc))
        ]
    html_text = HTML_SHELL.format(
        title=html_lib.escape(pdf_path.stem),
        body="\n".join(pages_html),
    )

    _atomic_write_text(html_path, html_text, encoding="utf-8")
