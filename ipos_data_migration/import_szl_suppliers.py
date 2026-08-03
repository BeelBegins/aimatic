import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe

# --- Edit these before each run -------------------------------------------------
TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/vendordataghouritown.xlsx"
SUPPLIER_GROUP = "Distributor"
# Opening balances are dated on the planned SZL go-live/cutover date.
POSTING_DATE = "2026-08-04"
# The legacy workbook has no branch column. All Ghouri Town balances are S1.
BRANCH_OVERRIDE = "S1 - Ghouri Town VIP"
WHT_RATE_FROM_DATE = "2020-01-01"
WHT_RATE_TO_DATE = "2030-12-31"
# Only two Tax Withholding Groups: Filers / Non-Filers. Everything that isn't
# legacy FBRTYPE "FILER" (NONFILER, EXEMPT, NO) folds into Non-Filers.
FBRTYPE_TO_WHT_GROUP = {
    "FILER": "Filers",
    "NONFILER": "Non-Filers",
    "EXEMPT": "Non-Filers",
    "NO": "Non-Filers",
}
# Same 7-char alphanumeric shape supplier_management/events.py requires. A
# StandardNTN that doesn't match this (blank, truncated, stray characters) is
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
        supplier_code = str(r.get("SupplierCode", "")).strip()
        supplier_name = str(r.get("SupplierName", "")).strip()
        # The linked workbook has one final blank-code summary row whose
        # ClosingBalance repeats the file total. It is not a vendor.
        if not supplier_code and not supplier_name:
            continue

        standard_ntn = str(r.get("StandardNTN", "")).strip()
        # Malformed StandardNTN values (present but not the 7-char shape -- some
        # look like a SupplierCode that leaked into the NTN column) are nulled
        # out here, before grouping, so the garbage value is never persisted
        # anywhere (not as tax_id, not in the opening-entry remark). Falls
        # through to the same no-NTN/standalone/Non-Filers rule as a blank NTN.
        if standard_ntn and not NTN_PATTERN.match(standard_ntn):
            standard_ntn = ""

        rows.append(
            {
                "supplier_code": str(r.get("SupplierCode", "")).strip(),
                "supplier_name": str(r.get("SupplierName", "")).strip(),
                "fbrtype": str(r.get("FBRTYPE", "")).strip() or "NO",
                "contact_person": str(r.get("ContactPerson", "")).strip(),
                "contact_number": str(r.get("ContactNumber", "")).strip(),
                "ntno": str(r.get("NTNo", "")).strip(),
                "standard_ntn": standard_ntn,
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
    """Group legacy rows by StandardNTN (already nulled-out for malformed
    values in load_rows). Rows with no NTN each form their own single-row
    group -- nothing to merge, no stable/legitimate key to merge them on."""
    groups = collections.OrderedDict()
    for row in rows:
        ntn = row["standard_ntn"]
        key = ntn if ntn else f"__standalone__{row['supplier_code']}"
        groups.setdefault(key, []).append(row)
    return groups


def pick_winner(members):
    """The row that decides the merged Supplier's name/FBRTYPE/WhtTax%/contact
    info when a group has more than one legacy row: highest |debit + credit|
    activity."""
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
    # WhtTax% = 0 -> "Exempt" rather than "WHT 0%" (see apps/aimatic/ipos_data_migration/supplierimport.md).
    # Applies regardless of Filers/Non-Filers group.
    label = "Exempt" if not rate else f"WHT {rate:g}%"
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


def create_contact(winner, supplier_name):
    """New for siezal (see supplierimport.md, "Contact mapping"): a linked
    Contact record from the winning row's ContactPerson/ContactNumber, since
    the fill rate here is much higher than the hsm sample that justified
    dropping these fields there."""
    if not winner["contact_person"] and not winner["contact_number"]:
        return False

    contact = frappe.new_doc("Contact")
    contact.first_name = winner["contact_person"].title() if winner["contact_person"] else supplier_name
    if winner["contact_number"]:
        contact.append(
            "phone_nos",
            {"phone": winner["contact_number"], "is_primary_phone": 1},
        )
    contact.append("links", {"link_doctype": "Supplier", "link_name": supplier_name})
    contact.insert(ignore_permissions=True)
    return True


def create_suppliers(groups, company, wht_account):
    """Phase 1: one Supplier per NTN group (or per standalone no-NTN row).
    Returns (supplier_code -> supplier name map, stats, conflicts, failures)."""
    code_to_supplier = {}
    stats = collections.Counter()
    conflicts = []
    failures = []

    for key, members in groups.items():
        winner = pick_winner(members)
        # key is only ever a real (already-validated) NTN or a "__standalone__"
        # marker -- never trust a member row's raw standard_ntn directly.
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

                if create_contact(winner, supplier_name):
                    stats["contacts_created"] += 1

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


def resolve_company_branch(company):
    """Journal Entry has no doc_events hook for aimatic.branch_management (unlike
    Sales/Purchase/Stock Entry, which get branch/cost_center forced onto them by
    apply_branch_defaults) -- so unlike the item import's Stock Entries, nothing
    populates Journal Entry Account.branch here unless this script sets it
    itself. Confirmed live on siezal before this fix: cost_center on the 1,124
    legacy opening-balance JE lines came out correct only by coincidence (the
    Company's own default cost center happened to already point at the site's
    one Branch), while branch was blank on 100% of them -- the same
    unrecoverable gap documented in the root CLAUDE.md's GL Entry branch-backfill
    note. Mirrors apply_branch_defaults's own rule: exactly one Company Branch
    is used automatically, more than one requires an explicit decision here
    rather than a guess (legacy vendor-ledger rows carry no branch/location
    column at all, so there is no per-row signal to split on)."""
    if BRANCH_OVERRIDE:
        branch_company = frappe.db.get_value("Branch", BRANCH_OVERRIDE, "company")
        if branch_company != company:
            frappe.throw(f"Branch {BRANCH_OVERRIDE} is not configured for {company}.")
        cost_center = frappe.get_cached_value("Branch", BRANCH_OVERRIDE, "cost_center")
        if not cost_center:
            frappe.throw(f"Branch {BRANCH_OVERRIDE} has no Cost Center configured.")
        return BRANCH_OVERRIDE, cost_center

    branches = frappe.get_all("Branch", filters={"company": company}, pluck="name", order_by="name")
    if not branches:
        frappe.throw(f"No Branch configured for {company}; set one up before importing opening balances.")
    if len(branches) > 1:
        frappe.throw(
            f"{company} has multiple Branches ({', '.join(branches)}). Legacy vendor ledger rows "
            "carry no branch/location column, so this script cannot guess which one opening "
            "balances belong to -- pick one explicitly (e.g. add a BRANCH override constant) "
            "before running this import."
        )
    branch = branches[0]
    cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
    if not cost_center:
        frappe.throw(f"Branch {branch} has no Cost Center configured.")
    return branch, cost_center


def create_opening_entries(rows, code_to_supplier, company, posting_date):
    """Phase 2: one Journal Entry per legacy row with a nonzero closing balance,
    each carrying its own original vendor name/code in the remark so the merged
    Supplier's ledger still shows exactly where every rupee came from. Negative
    balances (advance/overpayment) are allowed and expected."""
    branch, cost_center = resolve_company_branch(company)
    payable_account = frappe.get_cached_value("Company", company, "default_payable_account")
    # Don't assume "Temporary Opening - <abbr>" is the account's full name --
    # siezal's chart of accounts has it as "1910 - Temporary Opening - SSM"
    # (numeric prefix included), unlike the hsm site this script was modeled
    # on. Resolve by account_name instead, same approach already used for the
    # WHT payable account's "Duties and Taxes" parent lookup.
    temp_opening_account = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
    )
    if not temp_opening_account:
        frappe.throw(f"No 'Temporary Opening' account found under {company}.")

    stats = collections.Counter()
    failures = []

    for row in rows:
        # Round before the zero-check: TotalCredit - TotalDebit can leave a
        # float dust remainder that's truthy here but rounds to 0.00 at
        # ERPNext's currency precision, which then fails the JE.
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
                        "branch": branch,
                        "cost_center": cost_center,
                    },
                )
                je.append(
                    "accounts",
                    {
                        "account": temp_opening_account,
                        "debit_in_account_currency": amount,
                        "branch": branch,
                        "cost_center": cost_center,
                    },
                )
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
                        "branch": branch,
                        "cost_center": cost_center,
                    },
                )
                je.append(
                    "accounts",
                    {
                        "account": temp_opening_account,
                        "credit_in_account_currency": amount,
                        "branch": branch,
                        "cost_center": cost_center,
                    },
                )

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


def assert_target_site():
    if frappe.local.site != TARGET_SITE:
        frappe.throw(f"This importer is locked to site {TARGET_SITE}, not {frappe.local.site}.")


def run():
    assert_target_site()
    posting_date = POSTING_DATE or frappe.utils.today()
    print(f"*** Posting date for opening entries: {posting_date} ***")

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
