from __future__ import annotations

import os

import frappe

# Centralized home for every print format's actual HTML/CSS/Jinja layout -
# plain .html files, not embedded as one giant escaped string inside each
# Print Format's own JSON. Add a new layout by dropping a file here and
# pointing that Print Format's html field at render_print_layout(name, doc)
# - no new hook is ever needed for the layout itself, only this one loader.
LAYOUTS_DIR = os.path.join(os.path.dirname(__file__), "print_layouts")


def render_print_layout(name, doc, **context):
    """Read print_layouts/<name>.html and render it against doc plus
    whatever extra context a specific layout needs (e.g. ctx=<domain-specific
    lookups>, layout=<Frappe's own page layout>, print_settings=...)."""
    path = os.path.join(LAYOUTS_DIR, f"{name}.html")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    return frappe.render_template(source, {"doc": doc, **context})
