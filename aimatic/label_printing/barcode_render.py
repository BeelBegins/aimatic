import base64
from io import BytesIO

import frappe

BARCODE_TYPE_MAP = {
    "Code128": "code128",
    "EAN13": "ean13",
}


def get_barcode_data_uri(value, barcode_type="Code128", height_mm=10, module_width_mm=0.33):
    """Render a barcode value to a PNG data URI using python-barcode + Pillow.

    Used from print format Jinja templates so both the on-screen preview and
    the PDF export (wkhtmltopdf) show an identical, scannable barcode image
    with no client-side JS dependency.
    """
    if not value:
        return ""

    try:
        import barcode
        from barcode.writer import ImageWriter
    except ImportError:
        frappe.log_error(
            title="Label Printing: python-barcode not installed",
            message="Install the 'python-barcode' package to render barcode images.",
        )
        return ""

    key = BARCODE_TYPE_MAP.get(barcode_type, "code128")

    try:
        barcode_class = barcode.get_barcode_class(key)
        code = barcode_class(str(value), writer=ImageWriter())
    except Exception:
        # EAN13 requires a numeric value of a specific length; fall back to
        # Code128 (which accepts any text) rather than failing the whole label.
        barcode_class = barcode.get_barcode_class("code128")
        code = barcode_class(str(value), writer=ImageWriter())

    dpi = 203  # matches TSC 244 Plus native resolution
    options = {
        "module_height": height_mm,
        "module_width": module_width_mm,
        "quiet_zone": 1.0,
        "font_size": 0,
        "text_distance": 0,
        "write_text": False,
        "dpi": dpi,
    }

    buffer = BytesIO()
    code.write(buffer, options=options)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
