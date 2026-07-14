import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe

# --- Edit these before each run -------------------------------------------------
FILE_PATH = "/home/nabeel/frappe-bench/sites/hsm/public/files/schema.xlsx"
SUPPLIER_GROUP = "Distributor"
# None -> uses today(). Before a REAL migration cutover, set an explicit date,
# e.g. "2026-08-01", so opening balances land on the correct go-live date.
POSTING_DATE = None
WHT_RATE_FROM_DATE = "2020-01-01"
WHT_RATE_TO_DATE = "2030-12-31"
# Only two Tax Withholding Groups: Filers / Non-Filers. Everything that isn't
# legacy FBRTYPE "FILER" (NONFILER, EXEMPT, NO) folds into Non-Filers.
# supplier_management/events.py's validate_supplier_ntn hook checks the literal
# string "Filers" (doc.tax_withholding_group == "Filers") to enforce the 7-char
# NTN format -- the Filers group created here must match that exactly, or
# imported filers silently skip NTN validation on every save.
FBRTYPE_TO_WHT_GROUP = {
    "FILER": "Filers",
    "NONFILER": "Non-Filers",
    "EXEMPT": "Non-Filers",
    "NO": "Non-Filers",
}
# Same 7-char alphanumeric shape supplier_management/events.py requires. A
# StandardNTN that doesn't match this (blank, truncated, stray characters --
# e.g. a source row seen with ' 848388', 6 digits and a leading space) is
# treated exactly like "no NTN": not used to group/merge, not stored as
# tax_id, and forced to Non-Filers regardless of FBRTYPE.
NTN_PATTERN = re.compile(r"^[A-Za-z0-9]{7}$")
# ---------------------------------------------------------------------------------

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_to_idx(col):
    value = 0
    for ch in col:
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def parse_first_sheet_rows(path):
    with zipfile.ZipFile(path) as zf:
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", NS):
                shared_strings.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pr:Relationship", NS)}

        first_sheet = workbook.find("a:sheets/a:sheet", NS)
        target = rel_map[first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
        if not target.startswith("xl/"):
            target = f"xl/{target}"

        worksheet = ET.fromstring(zf.read(target))

        header = None
        rows = []
        for row in worksheet.findall("a:sheetData/a:row", NS):
            values_by_index = {}
            max_index = -1
            for cell in row.findall("a:c", NS):
                ref = cell.attrib["r"]
                col = re.match(r"[A-Z]+", ref).group(0)
                idx = col_to_idx(col)
                max_index = max(max_index, idx)
                cell_type = cell.attrib.get("t")
                value_node = cell.find("a:v", NS)
                if value_node is None:
                    value = ""
                elif cell_type == "s":
                    value = shared_strings[int(value_node.text)]
                else:
                    value = value_node.text or ""
                values_by_index[idx] = value

            values = [values_by_index.get(i, "") for i in range(max_index + 1)]
            if header is None:
                header = values
                continue
            if not any(str(v).strip() for v in values):
                continue

            record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
            record["_rownum"] = int(row.attrib.get("r", "0"))
            rows.append(record)

        return rows


def as_float(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    return float(text)


def load_rows():
    raw = parse_first_sheet_rows(FILE_PATH)
    rows = []
    for r in raw:
        rows.append(
            {
                "supplier_code": str(r.get("SupplierCode", "")).strip(),
                "supplier_name": str(r.get("SupplierName", "")).strip(),
                "fbrtype": str(r.get("FBRTYPE", "")).strip() or "NO",
                "contact_person": str(r.get("ContactPerson", "")).strip(),
                "ntno": str(r.get("NTNo", "")).strip(),
                "standard_ntn": str(r.get("StandardNTN", "")).strip(),
                "wht_rate": as_float(r.get("WhtTax%")),
                "ledger_code": str(r.get("LedgerCode", "")).strip(),
                "total_debit": as_float(r.get("TotalDebit")),
                "total_credit": as_float(r.get("TotalCredit")),
                "closing_balance": as_float(r.get("ClosingBalance")),
                "_rownum": r.get("_rownum"),
            }
        )
    return rows


def build_groups(rows):
    """Group legacy rows by StandardNTN. Rows with no NTN, or a StandardNTN that
    doesn't match the valid 7-char NTN shape, each form their own single-row
    group (nothing to merge, no stable/legitimate key to merge them on)."""
    groups = collections.OrderedDict()
    for row in rows:
        ntn = row["standard_ntn"]
        key = ntn if ntn and NTN_PATTERN.match(ntn) else f"__standalone__{row['supplier_code']}"
        groups.setdefault(key, []).append(row)
    return groups


def pick_winner(members):
    """The row that decides the merged Supplier's name/FBRTYPE/WhtTax% when a
    group has more than one legacy row: highest |debit + credit| activity."""
    return max(members, key=lambda r: abs(r["total_debit"]) + abs(r["total_credit"]))


def get_company():
    companies = frappe.get_all("Company", pluck="name")
    if len(companies) != 1:
        frappe.throw(
            f"Expected exactly one Company on this site to default to, found {companies}. "
            "Edit get_company() to pick the right one before running."
        )
    return companies[0]


def get_or_create_wht_payable_account(company):
    abbr = frappe.get_cached_value("Company", company, "abbr")
    account_name = f"Withholding Tax Payable - {abbr}"
    if frappe.db.exists("Account", account_name):
        return account_name

    parent = frappe.db.get_value("Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1})
    if not parent:
        frappe.throw(f"No 'Duties and Taxes' group account found under {company} to attach the WHT payable account to.")

    account = frappe.new_doc("Account")
    account.account_name = "Withholding Tax Payable"
    account.company = company
    account.parent_account = parent
    account.account_type = "Tax"
    account.insert(ignore_permissions=True)
    return account.name


def get_or_create_wht_group(fbrtype):
    label = FBRTYPE_TO_WHT_GROUP.get(fbrtype, fbrtype)
    if frappe.db.exists("Tax Withholding Group", label):
        return label
    doc = frappe.new_doc("Tax Withholding Group")
    doc.group_name = label
    doc.insert(ignore_permissions=True)
    return doc.name


def get_or_create_wht_category(rate, company, wht_account):
    label = f"WHT {rate:g}%"
    if frappe.db.exists("Tax Withholding Category", label):
        return label

    doc = frappe.new_doc("Tax Withholding Category")
    doc.name = label
    doc.category_name = label
    doc.tax_deduction_basis = "Gross Total"
    doc.append(
        "rates",
        {
            "from_date": WHT_RATE_FROM_DATE,
            "to_date": WHT_RATE_TO_DATE,
            "tax_withholding_rate": rate,
        },
    )
    doc.append("accounts", {"company": company, "account": wht_account})
    doc.insert(ignore_permissions=True)
    return doc.name


def create_suppliers(groups, company, wht_account):
    """Phase 1: one Supplier per NTN group (or per standalone no-NTN row).
    Returns (supplier_code -> supplier name map, stats, conflicts, failures)."""
    code_to_supplier = {}
    stats = collections.Counter()
    conflicts = []
    failures = []

    for key, members in groups.items():
        winner = pick_winner(members)
        # key is only ever a real NTN (build_groups already validated its shape)
        # or a "__standalone__" marker -- never trust a member row's raw
        # standard_ntn directly, it may be present but malformed.
        tax_id = key if not key.startswith("__standalone__") else None
        legacy_codes = ",".join(m["supplier_code"] for m in members)

        # Compare on the *mapped* group (Filers/Non-Filers), not the raw FBRTYPE --
        # NONFILER vs EXEMPT vs NO all collapse to Non-Filers and aren't a real conflict.
        wht_groups = {FBRTYPE_TO_WHT_GROUP.get(m["fbrtype"], m["fbrtype"]) for m in members}
        wht_rates = {m["wht_rate"] for m in members}
        if len(wht_groups) > 1 or len(wht_rates) > 1:
            conflicts.append(
                {
                    "ntn": key,
                    "winner_code": winner["supplier_code"],
                    "wht_groups": sorted(wht_groups),
                    "wht_rates": sorted(wht_rates),
                    "members": [(m["supplier_code"], m["supplier_name"], m["fbrtype"], m["wht_rate"]) for m in members],
                }
            )

        try:
            existing = None
            if tax_id:
                existing = frappe.db.get_value("Supplier", {"tax_id": tax_id})
            else:
                existing = frappe.db.get_value("Supplier", {"custom_legacy_supplier_code": legacy_codes})

            if existing:
                supplier_name = existing
                stats["suppliers_reused"] += 1
            else:
                # A row can only be validated as "Filers" if it actually has an NTN.
                # No StandardNTN at all -> Non-Filers regardless of what FBRTYPE claims,
                # since validate_supplier_ntn would otherwise reject the missing tax_id.
                fbrtype_for_group = winner["fbrtype"] if tax_id else "NONFILER"
                wht_group = get_or_create_wht_group(fbrtype_for_group)
                wht_category = get_or_create_wht_category(winner["wht_rate"], company, wht_account)

                supplier_name_value = winner["supplier_name"]
                if frappe.db.exists("Supplier", supplier_name_value):
                    supplier_name_value = f"{supplier_name_value} ({tax_id or legacy_codes})"

                supplier = frappe.new_doc("Supplier")
                supplier.supplier_name = supplier_name_value
                supplier.supplier_group = SUPPLIER_GROUP
                supplier.supplier_type = "Company"
                if tax_id:
                    supplier.tax_id = tax_id
                supplier.custom_legacy_supplier_code = legacy_codes
                supplier.tax_withholding_group = wht_group
                supplier.tax_withholding_category = wht_category
                supplier.insert(ignore_permissions=True)

                supplier_name = supplier.name
                stats["suppliers_created"] += 1

            for member in members:
                code_to_supplier[member["supplier_code"]] = supplier_name

        except Exception as exc:
            frappe.db.rollback()
            stats["group_failed"] += 1
            failures.append({"ntn": key, "codes": legacy_codes, "reason": str(exc)})
            print(f"FAILED group NTN={key} codes={legacy_codes} -> {exc}")
            continue

        frappe.db.commit()

    return code_to_supplier, stats, conflicts, failures


def create_opening_entries(rows, code_to_supplier, company, posting_date):
    """Phase 2: one Journal Entry per legacy row with a nonzero closing balance,
    each carrying its own original vendor name/code in the remark so the merged
    Supplier's ledger still shows exactly where every rupee came from."""
    payable_account = frappe.get_cached_value("Company", company, "default_payable_account")
    abbr = frappe.get_cached_value("Company", company, "abbr")
    temp_opening_account = f"Temporary Opening - {abbr}"

    stats = collections.Counter()
    failures = []

    for row in rows:
        # Round before the zero-check: TotalCredit - TotalDebit can leave a
        # float dust remainder (e.g. 1e-9) that's truthy here but rounds to
        # 0.00 at ERPNext's currency precision, which then fails the JE with
        # "Both Debit and Credit values cannot be zero".
        balance = frappe.utils.flt(row["closing_balance"], 2)
        if not balance:
            continue

        supplier = code_to_supplier.get(row["supplier_code"])
        if not supplier:
            stats["skipped_no_supplier"] += 1
            continue

        reference = f"LEGACY-OB-{row['supplier_code']}"
        if frappe.db.exists("Journal Entry", {"cheque_no": reference, "docstatus": ["!=", 2]}):
            stats["entries_reused"] += 1
            continue

        remark = (
            f"Legacy vendor {row['supplier_name']} (Code {row['supplier_code']}, "
            f"Ledger {row['ledger_code']}, NTN {row['standard_ntn'] or 'n/a'})"
        )
        amount = abs(balance)

        try:
            je = frappe.new_doc("Journal Entry")
            je.voucher_type = "Opening Entry"
            je.company = company
            je.posting_date = posting_date
            je.is_opening = "Yes"
            je.cheque_no = reference
            je.cheque_date = posting_date
            je.user_remark = remark

            if balance > 0:
                # net credit in the legacy ledger -> we owe the supplier
                je.append(
                    "accounts",
                    {
                        "account": payable_account,
                        "party_type": "Supplier",
                        "party": supplier,
                        "credit_in_account_currency": amount,
                        "user_remark": remark,
                    },
                )
                je.append("accounts", {"account": temp_opening_account, "debit_in_account_currency": amount})
            else:
                # net debit -> advance / overpayment to the supplier
                je.append(
                    "accounts",
                    {
                        "account": payable_account,
                        "party_type": "Supplier",
                        "party": supplier,
                        "debit_in_account_currency": amount,
                        "user_remark": remark,
                    },
                )
                je.append("accounts", {"account": temp_opening_account, "credit_in_account_currency": amount})

            je.insert(ignore_permissions=True)
            je.submit()
            stats["entries_created"] += 1

        except Exception as exc:
            frappe.db.rollback()
            stats["entries_failed"] += 1
            failures.append({"code": row["supplier_code"], "name": row["supplier_name"], "reason": str(exc)})
            print(f"FAILED opening entry for {row['supplier_code']} ({row['supplier_name']}) -> {exc}")
            continue

        frappe.db.commit()

    return stats, failures


def run():
    posting_date = POSTING_DATE or frappe.utils.today()
    print(f"*** Posting date for opening entries: {posting_date} ***")
    print("*** Edit POSTING_DATE at the top of this script before a real migration cutover run. ***")

    company = get_company()
    wht_account = get_or_create_wht_payable_account(company)

    rows = load_rows()
    groups = build_groups(rows)
    print(f"Loaded {len(rows)} legacy rows -> {len(groups)} supplier groups")

    code_to_supplier, supplier_stats, conflicts, supplier_failures = create_suppliers(groups, company, wht_account)
    print("PHASE 1 (suppliers) SUMMARY", dict(supplier_stats))
    print("CONFLICTING GROUPS (FBRTYPE/WhtTax%% disagree within one NTN):", len(conflicts))
    for c in conflicts[:50]:
        print("CONFLICT", c)
    print("SUPPLIER FAILURES", len(supplier_failures))
    for f in supplier_failures[:50]:
        print("SUPPLIER FAILURE", f)

    entry_stats, entry_failures = create_opening_entries(rows, code_to_supplier, company, posting_date)
    print("PHASE 2 (opening balances) SUMMARY", dict(entry_stats))
    print("OPENING ENTRY FAILURES", len(entry_failures))
    for f in entry_failures[:50]:
        print("OPENING ENTRY FAILURE", f)


run()
