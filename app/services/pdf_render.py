"""Render PDF pages to PNG image bytes for the vision LLM (Slice 3)."""

from __future__ import annotations

import io

import pypdfium2 as pdfium

from app.config import settings


def render_pdf_to_images(pdf_bytes: bytes, max_pages: int, scale: float | None = None) -> list[bytes]:
    """Rasterize up to ``max_pages`` pages to PNG bytes (one per page)."""
    scale = scale if scale is not None else settings.render_scale
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        images: list[bytes] = []
        page_count = min(len(doc), max_pages)
        for i in range(page_count):
            page = doc[i]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="PNG")
            images.append(buf.getvalue())
        return images
    finally:
        doc.close()
