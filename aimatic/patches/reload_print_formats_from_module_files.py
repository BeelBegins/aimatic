import frappe

# These 8 Print Formats are now tracked as real per-module files
# (<module>/print_format/<name>/<name>.json), matching how Frappe/ERPNext
# ship their own standard print formats - this replaces the custom
# printingformats/formats.json + one-time-patch sync mechanism entirely.
FORMATS_TO_RELOAD = (
	("Label Printing", "AIM Barcode Label"),
	("Label Printing", "AIM Shelf Label A4"),
	("Aimatic", "AIM Stock Debit Note - Purchase Invoice"),
	("Aimatic", "AIM Stock Debit Note - Purchase Receipt"),
	("Aimatic", "POS 80x3276 v2"),
	("Aimatic", "POS Updated Print Layout"),
	("Aimatic", "pos80x3276"),
	("Aimatic", "Purchase Order Updated Lyaout"),
)


def execute():
	"""Force-reload each one now so every site's existing record (previously
	synced by the old custom mechanism, some with a stale module/standard
	value) picks up the corrected content in one deterministic pass. From
	here on, Frappe's own bench migrate already re-syncs these files
	automatically (frappe.model.sync.IMPORTABLE_DOCTYPES includes Print
	Format) - no patch is needed for future edits to any of these 8.
	"""
	for module, name in FORMATS_TO_RELOAD:
		frappe.reload_doc(module, "print_format", name, force=True)
