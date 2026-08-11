"""Shared barcode normalization helpers for scan/lookup surfaces."""


def barcode_variants(barcode: str | None) -> list[str]:
	"""Exact value plus common scanner/GTIN padding variants.

	Handheld scanners often emit EAN-13 with a leading 0 for UPC-A labels
	stored as 12 digits (and the reverse). Keep in sync with
	Posapplication ``src/core/barcode-variants.ts``.
	"""
	value = str(barcode or "").strip()
	if not value:
		return []
	variants = {value}
	if value.isdigit():
		stripped = value.lstrip("0") or "0"
		variants.add(stripped)
		if value.startswith("0") and len(value) > 1:
			variants.add(value[1:])
		else:
			variants.add("0" + value)
		for width in (12, 13, 14):
			variants.add(value.zfill(width))
			variants.add(stripped.zfill(width))
	return list(variants)
