frappe.ui.form.on("Stock Reconciliation", {
	refresh(frm) {
		// core's `setup` handler (stock_reconciliation.js) always runs before
		// `refresh`, so frm.barcode_scanner is guaranteed to exist here
		// regardless of app script load order.
		if (frm.barcode_scanner) {
			frm.barcode_scanner.scan_api = "aimatic.stock_reconciliation_barcode.scan_barcode";
		}
	},
});
