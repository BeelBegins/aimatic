from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

from aimatic.retail_operations.calculations import CHANNELS, webhook_valid
from aimatic.retail_operations.permissions import check


class OperationsDocument(Document):
	def validate(self):
		if self.doctype == "Retail Operations Settings":
			from urllib.parse import urlparse

			if self.public_base_url:
				value = urlparse(self.public_base_url)
				if (
					value.scheme != "https"
					or not value.hostname
					or value.username
					or value.password
					or value.query
					or value.fragment
					or value.path not in ("", "/")
				):
					frappe.throw("ERPNext Public URL must be an HTTPS origin without credentials or a path")
			if self.discord_enabled and not self.public_base_url:
				frappe.throw("Set the ERPNext Public URL before enabling Discord sending")
			for key in ("history_days", "fallback_cover_days", "max_margin_rows", "counter_min_receipts"):
				if flt(self.get(key)) <= 0:
					frappe.throw(f"{key.replace('_', ' ')} must be positive")
			if not 1 <= int(self.max_discord_pages or 8) <= 20:
				frappe.throw("Digest messages must be between 1 and 20 per destination")
			if self.history_days > 90 or self.fallback_cover_days > 28 or self.max_margin_rows > 20000:
				frappe.throw(
					"History is bounded to 90 days, fallback cover to 28 days and margin detail to 20,000 rows"
				)
			return
		if self.meta.istable:
			return
		if self.doctype in ("Retail Operations Run", "Retail Discord Delivery"):
			if not self.flags.operations_internal:
				frappe.throw("Generated operational records cannot be edited directly")
			return
		if not self.get("branch"):
			return
		branch = (
			frappe.get_doc("Branch", self.branch)
			if self.doctype in ("Retail POS Void Event", "Retail Operations Issue")
			and self.flags.operations_internal
			else check(self.branch)
		)
		self.company = branch.company
		if self.doctype == "Retail Operations Issue":
			if not self.flags.operations_internal:
				before = self.get_doc_before_save()
				if not before or any(
					self.get(k) != before.get(k)
					for k in (
						"branch",
						"channel",
						"issue_key",
						"evidence",
						"signal_hash",
						"run",
						"severity",
						"subject",
						"notified_hash",
						"last_notified",
					)
				):
					frappe.throw("Only issue status, reason and mute expiry can be changed")
				check(self.branch, self.channel)
				if not self.reason:
					frappe.throw("Record a reason for the issue decision")
				if self.status == "Muted" and (not self.mute_until or self.mute_until <= now_datetime()):
					frappe.throw("A future mute expiry is required")
		elif self.doctype == "Retail Purchase Commitment":
			check(self.branch, "Purchase", write=True)
			if self.warehouse != branch.finished_goods_warehouse:
				frappe.throw("Commitment warehouse must be this branch's finished-goods warehouse")
			if getdate(self.placed_on) > getdate():
				frappe.throw("Placed date cannot be in the future")
			if not self.items:
				frappe.throw("At least one item is required")
			for row in self.items:
				if flt(row.qty) <= 0:
					frappe.throw("Commitment quantities must be positive stock units")
				if row.purchase_order_item and row.purchase_receipt_item:
					frappe.throw("Match a commitment line through either PO or direct receipt, not both")
				for field, doctype, parenttype in (
					("purchase_order_item", "Purchase Order Item", "Purchase Order"),
					("purchase_receipt_item", "Purchase Receipt Item", "Purchase Receipt"),
				):
					if not row.get(field):
						row.set(field, None)
						continue
					native = frappe.db.get_value(
						doctype,
						row.get(field),
						["parent", "item_code", "warehouse", "stock_qty"],
						as_dict=True,
					)
					if field == "purchase_receipt_item" and frappe.db.exists(
						"Retail Commitment Receipt", {"purchase_receipt_item": row.get(field)}
					):
						frappe.throw("This receipt row is already allocated to a commitment")
					parent = frappe.get_doc(parenttype, native.parent) if native else None
					if (
						not native
						or parent.docstatus != 1
						or parent.supplier != self.supplier
						or parent.company != self.company
						or parent.branch != self.branch
						or native.item_code != row.item_code
						or native.warehouse != self.warehouse
					):
						frappe.throw(
							"Matched supply must be submitted for the same vendor, company, branch, item and warehouse"
						)
					if flt(native.stock_qty) < flt(row.qty) and field == "purchase_order_item":
						frappe.throw("Commitment cannot exceed its matched PO stock quantity")
			before = self.get_doc_before_save()
			if before and before.status in ("Completed", "Cancelled") and self.status != before.status:
				frappe.throw("Closed commitments cannot be reopened; create a new commitment")
			from aimatic.retail_operations.commitments import validate_allocations

			validate_allocations(self)
		elif self.doctype == "Retail Product Reference":
			check(self.branch, "Inventory", write=True)
			members = [r.item_code for r in self.items]
			if self.primary_item not in members or len(set(members)) != len(members) or len(members) < 2:
				frappe.throw(
					"Include the primary item and at least one distinct alternate reference exactly once"
				)
			units = {frappe.db.get_value("Item", item, "stock_uom") for item in members}
			if len(units) != 1:
				frappe.throw(
					"Reference grouping requires matching stock units; conversion is outside this release"
				)
			if self.status == "Confirmed":
				duplicates = frappe.db.sql(
					"""SELECT r.name FROM `tabRetail Product Reference` r
                    JOIN `tabRetail Reference Item` i ON i.parent=r.name
                    WHERE r.branch=%s AND r.status='Confirmed' AND r.name!=%s AND i.item_code IN %s LIMIT 1""",
					(self.branch, self.name or "", tuple(members)),
				)
				if duplicates:
					frappe.throw(
						"An item already belongs to another confirmed reference group in this branch"
					)
				self.reviewed_by = frappe.session.user
				self.reviewed_at = now_datetime()
		elif self.doctype == "Retail Replenishment Rule":
			check(self.branch, "Purchase", write=True)
			if bool(self.supplier) == bool(self.source_warehouse):
				frappe.throw("Choose exactly one approved supplier or source warehouse")
			if self.source_warehouse == branch.finished_goods_warehouse:
				frappe.throw("Transfer source and destination cannot be the same")
			if self.automatic_request and not self.reviewed:
				frappe.throw("Review this rule before enabling automatic requests")
			duplicate = frappe.db.exists(
				self.doctype,
				{"branch": self.branch, "item_code": self.item_code, "name": ["!=", self.name or ""]},
			)
			if duplicate:
				frappe.throw("A rule already exists for this item and branch")
		elif self.doctype == "Retail POS Void Event":
			if not self.flags.operations_internal:
				frappe.throw("Void events must come from the authenticated event API")


def validate_branch(doc, method=None):
	for channel in CHANNELS:
		field = "custom_discord_" + channel.lower()
		value = doc.get_password(field, raise_exception=False) if doc.meta.has_field(field) else None
		try:
			valid = not value or webhook_valid(value)
		except ValueError:
			valid = False
		if not valid:
			frappe.throw(f"{channel} must use a Discord HTTPS webhook URL")
