import frappe
from frappe.utils import cint

from aimatic.label_printing.utils import expand_print_rows

DEFAULT_TSPL_LABEL = """SIZE {width} mm, {height} mm
GAP {gap} mm, 0 mm
DIRECTION 1
DENSITY 8
SPEED 4
CLS
{barcode_line}
{text_lines}
PRINT 1,1
"""


def _mm_to_dots(value_mm, dpi=203):
    # TSC 244 Plus is a 203 dpi printer.
    dots_per_mm = dpi / 25.4
    return int(round(cint(value_mm) if isinstance(value_mm, bool) else value_mm * dots_per_mm))


def build_tspl_for_row(row, template, job):
    width = template.label_width_mm or 40
    height = template.label_height_mm or 25
    gap = 2

    barcode_x = _mm_to_dots(2)
    barcode_y = _mm_to_dots(2)
    barcode_height_dots = _mm_to_dots(template.barcode_height_mm or 10)

    barcode_value = row.barcode or row.item_code
    barcode_type = "128" if template.barcode_type == "Code128" else "EAN13"

    barcode_line = (
        f'BARCODE {barcode_x},{barcode_y},"{barcode_type}",{barcode_height_dots},'
        f'{1 if template.show_barcode_text else 0},0,2,2,"{barcode_value}"'
    )

    text_lines = []
    text_y = barcode_y + barcode_height_dots + _mm_to_dots(5)

    if template.show_item_name and row.item_name:
        text_lines.append(f'TEXT {barcode_x},{text_y},"3",0,1,1,"{row.item_name}"')
        text_y += _mm_to_dots(4)

    if template.show_item_code:
        text_lines.append(f'TEXT {barcode_x},{text_y},"2",0,1,1,"{row.item_code}"')
        text_y += _mm_to_dots(4)

    if template.show_price and row.price:
        text_lines.append(f'TEXT {barcode_x},{text_y},"3",0,1,1,"Price: {row.price}"')
        text_y += _mm_to_dots(4)

    if template.show_mrp and row.mrp:
        text_lines.append(f'TEXT {barcode_x},{text_y},"3",0,1,1,"MRP: {row.mrp}"')
        text_y += _mm_to_dots(4)

    if template.show_batch_no and row.batch_no:
        text_lines.append(f'TEXT {barcode_x},{text_y},"1",0,1,1,"Batch: {row.batch_no}"')
        text_y += _mm_to_dots(4)

    if template.show_mfg_date and row.final_mfg_date:
        text_lines.append(f'TEXT {barcode_x},{text_y},"1",0,1,1,"MFG: {row.final_mfg_date}"')
        text_y += _mm_to_dots(4)

    if template.show_expiry_date and row.final_expiry_date:
        text_lines.append(f'TEXT {barcode_x},{text_y},"1",0,1,1,"EXP: {row.final_expiry_date}"')
        text_y += _mm_to_dots(4)

    return (barcode_line, "\n".join(text_lines))


def generate_tspl_for_job(job):
    """Return TSPL command text for every printable label instance in the
    job (barcode labels repeated by qty). Uses the template's TSPL override
    (a Jinja string) if provided, else a sensible built-in default layout.
    """
    template = frappe.get_doc("AIM Label Template", job.template)

    if template.print_mode != "TSC TSPL":
        frappe.msgprint(
            frappe._(
                "Template {0} print mode is {1}, not TSC TSPL. Generating TSPL anyway."
            ).format(template.template_name, template.print_mode),
            indicator="orange",
            alert=True,
        )

    rows = expand_print_rows(job)
    if not rows:
        frappe.throw(frappe._("No printable label rows found."))

    blocks = []
    for row in rows:
        if template.tspl_template:
            block = frappe.render_template(
                template.tspl_template,
                {
                    "doc": job,
                    "template": template,
                    "row": row,
                    "barcode_value": row.barcode or row.item_code,
                    "final_mfg_date": row.final_mfg_date,
                    "final_expiry_date": row.final_expiry_date,
                },
            )
        else:
            barcode_line, text_lines = build_tspl_for_row(row, template, job)
            block = DEFAULT_TSPL_LABEL.format(
                width=template.label_width_mm or 40,
                height=template.label_height_mm or 25,
                gap=2,
                barcode_line=barcode_line,
                text_lines=text_lines,
            )
        blocks.append(block)

    return "\n".join(blocks)
