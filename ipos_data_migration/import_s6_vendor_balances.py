"""S6 Khalid Block vendor opening balances.

NTN-first. A clean NTN is 7 alphanumeric characters (letters allowed,
including a leading I). Pakistan NTNo form ``XXXXXXX-C`` recovers the 7-char
stem when Excel stripped leading zeros from StandardNTN.

Match unique clean NTN to one enabled Supplier. Unmatched clean NTN creates
a new Supplier with that tax_id. Brand text in parentheses is a Principal,
not a Supplier merge/rename. Sister-store SIEZAL* rows map by established
name, not NTN, and are posted.

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s6_vendor_balances.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import frappe
from openpyxl import load_workbook

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/1006vendorbalances.xlsx"
COMPANY = "Siezal Supermarket"
BRANCH = "S6 - Khalid Block"
POSTING_DATE = "2026-09-20"
REFERENCE_PREFIX = "LEGACY-OB-S6-"
SUPPLIER_GROUP = "Distributor"

NTN_PATTERN = re.compile(r"^[A-Z0-9]{7}$")
NTN_DASHED = re.compile(r"^([A-Z0-9]{7})-([A-Z0-9])$")
PAREN_PATTERN = re.compile(r"\(([^)]+)\)\s*$")
FBRTYPE_TO_WHT_GROUP = {
	"FILER": "Filers",
	"NONFILER": "Non-Filers",
	"EXEMPT": "Non-Filers",
	"NO": "Non-Filers",
}

# S7-style sister-store map: branch-local codes onto existing intercompany Suppliers.
SISTER_STORE_MAP = {
	"1003": "SIEZAL SUPERMARKET (DHA PH 1)",
	"1005": "SIEZAL SUPERMARKET (BAHRIA TOWN PHASE 8)",
	"1006": "SIEZAL SUPERMARKET BAHRIA PH8 (S6)",
	"1007": "SIEZAL SUPERMARKET BAHRIA PH6 (S7)",
	"30617": "SIEZAL SUPERMARKET (BAHRIA PH7)",
	"454": "SIEZAL SUPERMARKET (MISRIAL ROAD)",
	"614": "SIEZAL SUPERMARKET (KHANNA PULL)",
	"616": "SIEZAL SUPERMARKET (DHA PH 1)",
}

APPLY_REASONS = {"unique_clean_ntn", "unmatched_clean_ntn", "sister_store", "no_clean_ntn"}
# S7 already resolved these NTN formatting variants onto existing Suppliers.
NTN_EXISTING_ALIASES = {
	"3896741": "M/S SCOURER BRITE",  # stored tax_id G389674
	"I249921": "PRIME DISTRIBUTION NETWORK (ISLAMABAD TEA)",  # stored 1249921
}


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing S6 vendor import: expected {TARGET_SITE}, got {site}")
	if not Path(FILE_PATH).is_file():
		frappe.throw(f"Missing vendor file {FILE_PATH}")


def _norm_name(value) -> str:
	return " ".join(str(value or "").upper().split())


def _norm_ntn(value) -> str:
	text = str(value or "").strip()
	if text.endswith(".0") and text.replace(".", "", 1).replace("-", "").isdigit():
		text = text[:-2]
	return re.sub(r"[^A-Z0-9]", "", text.upper())


def _clean_ntn(standard_ntn, ntno) -> str:
	"""7-alnum NTN. Prefer NTNo stem ``XXXXXXX-C`` so leading zeros/letters survive Excel."""
	ntno_text = str(ntno or "").strip().upper().replace(" ", "")
	dashed = NTN_DASHED.match(ntno_text)
	if dashed:
		return dashed.group(1)
	for raw in (standard_ntn, ntno):
		normalized = _norm_ntn(raw)
		if NTN_PATTERN.match(normalized):
			return normalized
	return ""


def _as_float(value) -> float:
	try:
		return float(str(value or "").strip().replace(",", "") or 0)
	except (TypeError, ValueError):
		return 0.0


def _extract_principal(supplier_name: str) -> str | None:
	match = PAREN_PATTERN.search((supplier_name or "").strip())
	if not match:
		return None
	label = re.sub(r"\s+", " ", match.group(1)).strip()
	return label or None


def _is_sister_store(supplier_name: str) -> bool:
	return _norm_name(supplier_name).startswith("SIEZAL")


def _is_i1_variant(left: str, right: str) -> bool:
	if len(left) != 7 or len(right) != 7 or left == right:
		return False
	diffs = [(a, b) for a, b in zip(left, right) if a != b]
	return len(diffs) == 1 and set(diffs[0]) <= {"I", "1"}


def load_rows():
	wb = load_workbook(FILE_PATH, read_only=True, data_only=True)
	ws = wb.active
	it = ws.iter_rows(values_only=True)
	header = [str(h).strip() if h is not None else "" for h in next(it)]
	rows = []
	for rec_values in it:
		rec = {header[i]: (rec_values[i] if i < len(rec_values) else None) for i in range(len(header))}
		code = str(rec.get("SupplierCode") or "").strip()
		if code.endswith(".0") and code[:-2].isdigit():
			code = code[:-2]
		name = str(rec.get("SupplierName") or "").strip()
		if not code and not name:
			continue
		rows.append(
			{
				"code": code,
				"name": name,
				"fbrtype": str(rec.get("FBRTYPE") or "").strip(),
				"standard_ntn": str(rec.get("StandardNTN") or "").strip(),
				"ntno": str(rec.get("NTNo") or "").strip(),
				"ledger_code": str(rec.get("LedgerCode") or "").strip(),
				"wht": rec.get("WhtTax%"),
				"total_debit": _as_float(rec.get("TotalDebit")),
				"total_credit": _as_float(rec.get("TotalCredit")),
				"closing_balance": _as_float(rec.get("ClosingBalance")),
			}
		)
	wb.close()
	return rows


def _supplier_indexes():
	by_name = defaultdict(list)
	by_ntn = defaultdict(list)
	by_code = defaultdict(list)
	disabled = {}
	for row in frappe.get_all(
		"Supplier",
		fields=["name", "supplier_name", "tax_id", "custom_legacy_supplier_code", "disabled"],
	):
		by_name[_norm_name(row.name)].append(row.name)
		if row.supplier_name:
			by_name[_norm_name(row.supplier_name)].append(row.name)
		ntn = _clean_ntn(row.tax_id, None) or _norm_ntn(row.tax_id)
		if ntn:
			by_ntn[ntn].append(row.name)
		for code in str(row.custom_legacy_supplier_code or "").split(","):
			code = code.strip()
			if code:
				by_code[code].append(row.name)
		disabled[row.name] = int(row.disabled or 0)
	for mapping in (by_name, by_ntn, by_code):
		for key, values in mapping.items():
			mapping[key] = sorted(set(values))
	return by_name, by_ntn, by_code, disabled


def classify_rows(rows):
	_by_name, by_ntn, by_code, disabled = _supplier_indexes()
	classified = []
	for row in rows:
		ntn = _clean_ntn(row["standard_ntn"], row["ntno"])
		code_hits = by_code.get(row["code"], [])
		ntn_hits = by_ntn.get(ntn, []) if ntn else []
		enabled_ntn = [name for name in ntn_hits if not disabled.get(name)]
		disabled_ntn = [name for name in ntn_hits if disabled.get(name)]
		proposed = None
		if "TEST" in _norm_name(row["name"]):
			reason = "likely_test"
		elif _is_sister_store(row["name"]):
			proposed = SISTER_STORE_MAP.get(row["code"])
			reason = "sister_store" if proposed else "sister_store_unmapped"
		elif not ntn or not NTN_PATTERN.match(ntn):
			reason = "no_clean_ntn"
			name_key = _norm_name(row["name"])
			name_hits = [n for n in _by_name.get(name_key, []) if not disabled.get(n)]
			if len(name_hits) == 1:
				proposed = name_hits[0]
			elif len(code_hits) == 1 and not disabled.get(code_hits[0]) and _norm_name(code_hits[0]) == name_key:
				# Only reuse legacy-code hits when the Supplier name matches — codes collide across branches.
				proposed = code_hits[0]
		elif len(enabled_ntn) == 1:
			proposed = enabled_ntn[0]
			reason = "unique_clean_ntn"
		elif len(enabled_ntn) > 1:
			reason = "ambiguous_ntn"
		elif disabled_ntn:
			reason = "disabled_supplier_only"
		elif ntn in NTN_EXISTING_ALIASES and frappe.db.exists("Supplier", NTN_EXISTING_ALIASES[ntn]):
			proposed = NTN_EXISTING_ALIASES[ntn]
			reason = "unique_clean_ntn"
		else:
			reason = "unmatched_clean_ntn"
		classified.append(
			{
				**row,
				"clean_ntn": ntn,
				"reason": reason,
				"proposed": proposed,
				"principal": None if _is_sister_store(row["name"]) else _extract_principal(row["name"]),
				"ntn_hits": ntn_hits,
				"code_hits": code_hits,
			}
		)
	return classified


def _wht_group(fbrtype, has_ntn: bool) -> str:
	if not has_ntn:
		return "Non-Filers"
	return FBRTYPE_TO_WHT_GROUP.get((fbrtype or "").upper(), "Non-Filers")


def _wht_category(rate) -> str:
	rate = _as_float(rate)
	label = "Exempt" if not rate else f"WHT {rate:g}%"
	if not frappe.db.exists("Tax Withholding Category", label):
		frappe.throw(f"Missing Tax Withholding Category {label}")
	return label


def _ensure_supplier(row, stats):
	if row["reason"] == "sister_store":
		name = row["proposed"]
		if not frappe.db.exists("Supplier", name):
			supplier = frappe.new_doc("Supplier")
			supplier.supplier_name = name
			supplier.supplier_group = SUPPLIER_GROUP
			supplier.supplier_type = "Company"
			supplier.tax_withholding_group = "Non-Filers"
			supplier.tax_withholding_category = "Exempt"
			supplier.custom_legacy_supplier_code = row["code"]
			supplier.insert(ignore_permissions=True)
			frappe.db.commit()
			stats["suppliers_created"] += 1
			print("CREATED_SISTER", supplier.name, row["code"])
			return supplier.name
		stats["suppliers_reused"] += 1
		return name

	if row["reason"] == "unique_clean_ntn":
		stats["suppliers_reused"] += 1
		return row["proposed"]

	if row["reason"] == "no_clean_ntn":
		if not (row.get("name") or "").strip() and not row.get("code"):
			stats["skipped_blank"] += 1
			return None
		if row.get("proposed") and frappe.db.exists("Supplier", row["proposed"]):
			stats["suppliers_reused"] += 1
			return row["proposed"]
		supplier_name = (row["name"] or "").strip()
		if not supplier_name:
			supplier_name = f"S6 LEGACY CODE {row['code']}"
		if frappe.db.exists("Supplier", supplier_name):
			stats["suppliers_reused"] += 1
			return supplier_name
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = supplier_name
		supplier.supplier_group = SUPPLIER_GROUP
		supplier.supplier_type = "Company"
		supplier.custom_legacy_supplier_code = row["code"]
		supplier.tax_withholding_group = _wht_group(row["fbrtype"], False)
		supplier.tax_withholding_category = _wht_category(row["wht"])
		supplier.insert(ignore_permissions=True)
		frappe.db.commit()
		stats["suppliers_created"] += 1
		print("CREATED_NO_NTN_SUPPLIER", supplier.name, "code", row["code"])
		return supplier.name

	ntn = row["clean_ntn"]
	existing = frappe.db.get_value("Supplier", {"tax_id": ntn}, "name")
	if existing:
		stats["suppliers_reused"] += 1
		return existing
	supplier_name = row["name"].strip()
	if frappe.db.exists("Supplier", supplier_name):
		current = frappe.get_doc("Supplier", supplier_name)
		current_ntn = _norm_ntn(current.tax_id)
		if current.disabled:
			supplier_name = f"{supplier_name} ({ntn})"
		elif not current_ntn:
			current.tax_id = ntn
			current.tax_withholding_group = _wht_group(row["fbrtype"], True)
			current.tax_withholding_category = _wht_category(row["wht"])
			current.save(ignore_permissions=True)
			frappe.db.commit()
			stats["tax_id_stamped"] += 1
			stats["suppliers_reused"] += 1
			print("STAMPED_TAX_ID", current.name, ntn)
			return current.name
		elif _is_i1_variant(ntn, current_ntn):
			stats["ntn_i1_variant_reused"] += 1
			stats["suppliers_reused"] += 1
			print("REUSED_I1_VARIANT", current.name, "stored", current_ntn, "source", ntn)
			return current.name
		else:
			supplier_name = f"{supplier_name} ({ntn})"
	supplier = frappe.new_doc("Supplier")
	supplier.supplier_name = supplier_name
	supplier.supplier_group = SUPPLIER_GROUP
	supplier.supplier_type = "Company"
	supplier.tax_id = ntn
	supplier.custom_legacy_supplier_code = row["code"]
	supplier.tax_withholding_group = _wht_group(row["fbrtype"], True)
	supplier.tax_withholding_category = _wht_category(row["wht"])
	supplier.insert(ignore_permissions=True)
	frappe.db.commit()
	stats["suppliers_created"] += 1
	print("CREATED_SUPPLIER", supplier.name, "tax_id", ntn, "code", row["code"])
	return supplier.name


def _principal_key(label: str) -> str:
	return re.sub(r"\s+", "", (label or "").upper())


def _ensure_principal(supplier, row, stats):
	label = row.get("principal")
	if not label or not supplier:
		return
	wanted = _principal_key(label)
	principal_name = None
	for existing_p in frappe.get_all("Principal", fields=["name"]):
		if _principal_key(existing_p.name) == wanted:
			principal_name = existing_p.name
			break
	if not principal_name:
		doc = frappe.new_doc("Principal")
		doc.principal_name = label
		doc.disabled = 0
		doc.insert(ignore_permissions=True)
		principal_name = doc.name
		stats["principals_created"] += 1
		print("CREATED_PRINCIPAL", principal_name)
	else:
		stats["principals_existing"] += 1
	existing = {
		(r.principal or "").strip()
		for r in frappe.get_all(
			"Supplier Principal",
			filters={"parent": supplier, "parenttype": "Supplier"},
			fields=["principal"],
		)
	}
	if any(_principal_key(name) == wanted for name in existing):
		stats["principal_links_existing"] += 1
		return
	doc = frappe.get_doc("Supplier", supplier)
	doc.append("custom_principals", {"principal": principal_name, "legacy_supplier_code": row["code"]})
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	stats["principal_links_created"] += 1
	print("LINKED_PRINCIPAL", supplier, principal_name, row["code"])


def _cancel_and_delete_duplicate(duplicate: str, keep: str, stamp_tax_id: str | None, stats: Counter):
	if not frappe.db.exists("Supplier", duplicate):
		return
	jes = [
		r[0]
		for r in frappe.db.sql(
			"""SELECT DISTINCT parent FROM `tabJournal Entry Account`
			WHERE party_type='Supplier' AND party=%s AND docstatus=1""",
			duplicate,
		)
	]
	for name in jes:
		je = frappe.get_doc("Journal Entry", name)
		je.cancel()
		frappe.db.commit()
		stats["duplicate_jes_cancelled"] += 1
		print("CANCELLED_DUP_JE", name, duplicate)
	po = frappe.db.count("Purchase Order", {"supplier": duplicate})
	pi = frappe.db.count("Purchase Invoice", {"supplier": duplicate})
	if po or pi:
		frappe.throw(f"Refusing to delete {duplicate}: has PO/PI")
	frappe.delete_doc("Supplier", duplicate, ignore_permissions=True, force=1)
	frappe.db.commit()
	stats["duplicate_suppliers_deleted"] += 1
	print("DELETED_DUP_SUPPLIER", duplicate, "keep", keep)
	if stamp_tax_id and frappe.db.exists("Supplier", keep) and not frappe.db.get_value("Supplier", keep, "tax_id"):
		frappe.db.set_value("Supplier", keep, "tax_id", stamp_tax_id)
		frappe.db.set_value("Supplier", keep, "tax_withholding_group", "Filers")
		stats["tax_id_stamped"] += 1
		print("STAMPED_TAX_ID", keep, stamp_tax_id)


def _repair_suffix_duplicates(stats: Counter):
	return


def _append_legacy_code(supplier, code, stats):
	existing = frappe.db.get_value("Supplier", supplier, "custom_legacy_supplier_code") or ""
	codes = [part.strip() for part in existing.split(",") if part.strip()]
	if code not in codes:
		codes.append(code)
		frappe.db.set_value("Supplier", supplier, "custom_legacy_supplier_code", ",".join(codes))
		stats["legacy_codes_appended"] += 1
	else:
		stats["legacy_codes_already_present"] += 1


def _post_opening(row, supplier, payable_account, temp_opening_account, cost_center, stats, failures):
	created = []
	balance = frappe.utils.flt(row["closing_balance"], 2)
	if not balance:
		stats["skipped_zero_balance"] += 1
		return created
	reference = f"{REFERENCE_PREFIX}{row['code']}"
	existing = frappe.db.get_value(
		"Journal Entry", {"cheque_no": reference, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		stats["entries_reused"] += 1
		return [existing]
	remark = (
		f"Legacy S6 vendor {row['name']} (Code {row['code']}, Ledger {row['ledger_code']}, "
		f"NTN {row.get('clean_ntn') or 'n/a'})"
	)
	amount = abs(balance)
	try:
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Opening Entry"
		je.company = COMPANY
		je.posting_date = POSTING_DATE
		je.is_opening = "Yes"
		je.cheque_no = reference
		je.cheque_date = POSTING_DATE
		je.user_remark = remark
		if balance > 0:
			je.append(
				"accounts",
				{
					"account": payable_account,
					"party_type": "Supplier",
					"party": supplier,
					"credit_in_account_currency": amount,
					"user_remark": remark,
					"branch": BRANCH,
					"cost_center": cost_center,
				},
			)
			je.append(
				"accounts",
				{
					"account": temp_opening_account,
					"debit_in_account_currency": amount,
					"branch": BRANCH,
					"cost_center": cost_center,
				},
			)
		else:
			je.append(
				"accounts",
				{
					"account": payable_account,
					"party_type": "Supplier",
					"party": supplier,
					"debit_in_account_currency": amount,
					"user_remark": remark,
					"branch": BRANCH,
					"cost_center": cost_center,
				},
			)
			je.append(
				"accounts",
				{
					"account": temp_opening_account,
					"credit_in_account_currency": amount,
					"branch": BRANCH,
					"cost_center": cost_center,
				},
			)
		je.insert(ignore_permissions=True)
		je.submit()
		blank = frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabJournal Entry Account`
			WHERE parent=%s AND (IFNULL(cost_center,'')='' OR IFNULL(branch,'')='')""",
			je.name,
		)[0][0]
		if blank:
			frappe.throw(f"{je.name} missing S6 dimensions")
		frappe.db.commit()
		stats["entries_created"] += 1
		created.append(je.name)
		print("CREATED_JE", je.name, reference, supplier)
	except Exception as exc:
		frappe.db.rollback()
		stats["entries_failed"] += 1
		failures.append({"code": row["code"], "name": row["name"], "reason": str(exc)})
		print(f"FAILED opening entry for {row['code']} ({row['name']}) -> {exc}")
	return created


def main(apply: bool = False):
	_assert_site()
	rows = load_rows()
	classified = classify_rows(rows)
	counts = Counter(row["reason"] for row in classified)
	nonzero = [row for row in classified if abs(row["closing_balance"]) >= 0.005]
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"source_rows": len(rows),
			"reason_counts": dict(counts),
			"nonzero_balance_rows": len(nonzero),
			"expected_net_closing_balance": round(sum(row["closing_balance"] for row in rows), 2),
			"apply_eligible": sum(1 for row in classified if row["reason"] in APPLY_REASONS),
			"held": sum(1 for row in classified if row["reason"] not in APPLY_REASONS),
			"samples": {
				reason: [
					{
						"code": row["code"],
						"name": row["name"][:80],
						"ntn": row.get("clean_ntn") or row["standard_ntn"],
						"balance": round(row["closing_balance"], 2),
						"proposed": row["proposed"],
						"principal": row.get("principal"),
					}
					for row in classified
					if row["reason"] == reason
				][:12]
				for reason in counts
			},
		}
	)
	if not apply:
		print("DRY_RUN — apply posts unique_clean_ntn, creates unmatched_clean_ntn, posts sister_store.")
		return {"applied": False, "stats": counts}

	stats = Counter()
	_repair_suffix_duplicates(stats)
	classified = classify_rows(rows)

	cost_center = frappe.get_cached_value("Branch", BRANCH, "cost_center")
	if not cost_center:
		frappe.throw(f"Branch {BRANCH} has no Cost Center")
	payable_account = frappe.get_cached_value("Company", COMPANY, "default_payable_account")
	temp_opening_account = frappe.db.get_value(
		"Account", {"company": COMPANY, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not temp_opening_account:
		frappe.throw(f"No Temporary Opening account for {COMPANY}")

	failures = []
	created_suppliers = []
	created_jes = []
	for row in classified:
		if row["reason"] not in APPLY_REASONS:
			stats[f"skipped_{row['reason']}"] += 1
			continue
		if row["reason"] == "sister_store" and row["proposed"] and not frappe.db.exists("Supplier", row["proposed"]):
			frappe.throw(f"Mapped sister store missing: {row['proposed']} (code {row['code']})")
		before = frappe.db.exists("Supplier", {"tax_id": row["clean_ntn"]}) if row["clean_ntn"] else None
		supplier = _ensure_supplier(row, stats)
		if not supplier:
			continue
		if row["reason"] == "unmatched_clean_ntn" and not before:
			created_suppliers.append({"supplier": supplier, "tax_id": row["clean_ntn"], "code": row["code"]})
		if row["reason"] == "no_clean_ntn" and not before:
			created_suppliers.append({"supplier": supplier, "tax_id": None, "code": row["code"]})
		_append_legacy_code(supplier, row["code"], stats)
		_ensure_principal(supplier, row, stats)
		created_jes.extend(
			_post_opening(row, supplier, payable_account, temp_opening_account, cost_center, stats, failures)
		)
	print("SUMMARY", dict(stats))
	print("CREATED_SUPPLIERS", created_suppliers)
	print("FAILURES", failures)
	return {
		"applied": True,
		"stats": stats,
		"created_suppliers": created_suppliers,
		"failures": failures,
		"journal_entries": created_jes,
	}
