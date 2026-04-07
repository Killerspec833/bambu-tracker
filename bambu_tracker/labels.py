from __future__ import annotations

"""
Label generation: Code128 (for DYMO LabelManager 280) and QR codes.

Code128  — generated via python-barcode with ImageWriter (Pillow backend).
           Suitable for tape labels; the barcode_id (e.g. SPL00042) encodes
           cleanly at 12 mm tape width.

QR       — generated via qrcode; encodes the full scan URL so a phone
           camera can open the spool directly without a barcode-scanner app.
"""

import io
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Lazy imports so missing optional deps produce a clear error at call-time.
def _barcode_lib():  # type: ignore[return]
    import barcode  # type: ignore[import-untyped]
    from barcode.writer import ImageWriter  # type: ignore[import-untyped]
    return barcode, ImageWriter

def _qrcode_lib():  # type: ignore[return]
    import qrcode  # type: ignore[import-untyped]
    return qrcode


Symbology = Literal["code128", "qr"]


def generate_code128(barcode_id: str) -> bytes:
    """Return a PNG of a Code128 barcode for the given ID string."""
    barcode_mod, ImageWriter = _barcode_lib()
    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": 0.8,   # mm; narrow enough for 12 mm tape
            "module_height": 8.0,  # mm
            "font_size": 6,
            "text_distance": 1.5,
            "quiet_zone": 2.0,
            "dpi": 300,
            "write_text": True,
        }
    )
    code = barcode_mod.get("code128", barcode_id, writer=writer)
    buf = io.BytesIO()
    code.write(buf, options={"write_text": True})
    return buf.getvalue()


def generate_qr(barcode_id: str, base_url: str = "") -> bytes:
    """Return a PNG of a QR code. The payload is base_url + barcode_id,
    or just barcode_id when base_url is empty."""
    qrcode = _qrcode_lib()
    payload = f"{base_url.rstrip('/')}/scan/{barcode_id}" if base_url else barcode_id
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def label_png(barcode_id: str, symbology: Symbology, base_url: str = "") -> bytes:
    """Dispatch to the right generator."""
    if symbology == "code128":
        return generate_code128(barcode_id)
    return generate_qr(barcode_id, base_url=base_url)
