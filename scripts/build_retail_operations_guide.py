#!/usr/bin/env python3
"""Build the committed operator PDF from its canonical Markdown. Use bench Python."""

import html
import subprocess
import tempfile
from pathlib import Path

import markdown2

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/szl-purchase-inventory-guide.md"
OUTPUT = SOURCE.with_suffix(".pdf")
CSS = """
body { font-family: 'DejaVu Sans', sans-serif; font-size: 10pt; line-height: 1.48; color: #263445; }
h1 { font-size: 25pt; color: #123c50; margin-bottom: 8mm; }
h2 { font-size: 15pt; color: #123c50; border-bottom: 1px solid #c9d6dc; padding-bottom: 3mm; margin-top: 9mm; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #126d73; margin-top: 6mm; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: #126d73; text-decoration: none; }
table { width: 100%; border-collapse: collapse; font-size: 9pt; margin: 4mm 0; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th { background: #e9f1f3; color: #163d4d; text-align: left; }
th, td { padding: 2.5mm; border: 1px solid #d6e0e5; vertical-align: top; }
pre { font-family: 'DejaVu Sans Mono', monospace; font-size: 8pt; white-space: pre-wrap; background: #f2f5f7; padding: 4mm; page-break-inside: avoid; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.5pt; }
blockquote { border-left: 3px solid #126d73; padding-left: 4mm; color: #46616d; }
"""


def main():
	body = markdown2.markdown(SOURCE.read_text(), extras=["tables", "fenced-code-blocks"])
	document = (
		'<!doctype html><html><head><meta charset="utf-8"><title>'
		+ html.escape("SZL Purchase & Inventory Operating Guide")
		+ "</title><style>"
		+ CSS
		+ "</style></head><body>"
		+ body
		+ "</body></html>"
	)
	with tempfile.TemporaryDirectory(prefix="szl-guide-") as directory:
		source = Path(directory) / "guide.html"
		source.write_text(document)
		subprocess.run(
			[
				"wkhtmltopdf",
				"--quiet",
				"--disable-javascript",
				"--page-size",
				"A4",
				"--margin-top",
				"16mm",
				"--margin-bottom",
				"18mm",
				"--margin-left",
				"15mm",
				"--margin-right",
				"15mm",
				"--title",
				"SZL Purchase & Inventory Operating Guide",
				str(source),
				str(OUTPUT),
			],
			check=True,
		)
	print(OUTPUT)


if __name__ == "__main__":
	main()
