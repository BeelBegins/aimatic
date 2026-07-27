"""One-time catalog replication: Item Group tree, Brand master, Item master,
and Item Barcode child rows from the live production site `siezal` onto
`szl` (both companies are "Siezal Super Market"/SSM -- szl is the real
production site going forward, S1 included; see setup_szl.md).

Scope, deliberately narrow (master data only, matching the "cover master
data today" ask -- on-hand stock and supplier balances are separate, later
steps):
- Item Group: full tree (106 rows on siezal), via normal Document.insert()
  in parent-first order so szl's own NestedSet lft/rgt is computed correctly
  (never copy lft/rgt values directly across sites -- they're meaningless
  outside the tree they were computed for).
- Brand: flat master (831 rows), via normal Document.insert().
- Item: bulk raw-SQL cross-database copy (16,494 rows -- looping through
  frappe.get_doc().insert() for this volume would be impractically slow and
  is unnecessary since siezal's rows are already-valid production data).
  Explicit column list by name, not position -- confirmed same 91 columns on
  both sites but in a different physical order, so a blind `INSERT ...
  SELECT *` would silently scramble data into the wrong columns.
- Item Barcode: bulk raw-SQL copy of the child table (18,247 rows), same
  reasoning.
- Item Default (only 2 rows on siezal, one pointing at the disabled generic
  "Stores - SSM" warehouse) and Item Supplier (1 row) are deliberately
  EXCLUDED -- both are company/warehouse or supplier-specific and neither
  has enough real data to be worth the complexity of reconciling company-
  specific defaults across sites right now.
- Item Reorder, Item Customer Detail, Item Website Specification, Item Tax,
  Item Group Defaults/Taxes: all 0 rows on siezal, nothing to copy.

Run via bench console (same convention as the setup_szl_*.py scripts --
this directory has no __init__.py / isn't importable via `bench execute`):
    exec(open("apps/aimatic/ipos_data_migration/import_siezal_catalog_to_szl.py").read())
    main()

Requires DB root credentials (from sites/common_site_config.json) for the
cross-database SQL steps -- the site-specific DB users are scoped to their
own database only.
"""

import frappe
import MySQLdb

SIEZAL_DB = "_5024a81034978647"
SZL_DB = "_2b4dd7884d505f7f"

def _database_password():
    password = frappe.conf.get("root_password") or frappe.conf.get("mariadb_root_password")
    if not password:
        frappe.throw(
            "Database root password is not configured in sites/common_site_config.json"
        )
    return password



def import_item_groups():
    # frappe.db here is szl's own connection (this script runs inside a szl
    # bench console session) -- read siezal's tree via a raw cross-database
    # query instead, since we can't frappe.init() a second site mid-process.
    conn = MySQLdb.connect(host="127.0.0.1", user="root", password=_database_password(), database=SIEZAL_DB)
    try:
        with conn.cursor(MySQLdb.cursors.DictCursor) as cur:
            cur.execute(
                """
                select name, item_group_name, parent_item_group, is_group
                from `tabItem Group`
                where name != 'All Item Groups'
                order by lft
                """
            )
            siezal_groups = cur.fetchall()
    finally:
        conn.close()

    created = 0
    skipped = 0
    for row in siezal_groups:
        if frappe.db.exists("Item Group", row["item_group_name"]):
            skipped += 1
            continue
        doc = frappe.new_doc("Item Group")
        doc.item_group_name = row["item_group_name"]
        doc.parent_item_group = row["parent_item_group"]
        doc.is_group = row["is_group"]
        doc.insert(ignore_permissions=True)
        created += 1
    frappe.db.commit()
    print(f"Item Group: {created} created, {skipped} already present")


def import_brands():
    conn = MySQLdb.connect(host="127.0.0.1", user="root", password=_database_password(), database=SIEZAL_DB)
    try:
        with conn.cursor(MySQLdb.cursors.DictCursor) as cur:
            cur.execute("select distinct brand from tabItem where brand is not null and brand != ''")
            siezal_brands = [r["brand"] for r in cur.fetchall()]
    finally:
        conn.close()

    created = 0
    skipped = 0
    for brand in siezal_brands:
        if frappe.db.exists("Brand", brand):
            skipped += 1
            continue
        doc = frappe.new_doc("Brand")
        doc.brand = brand
        doc.insert(ignore_permissions=True)
        created += 1
    frappe.db.commit()
    print(f"Brand: {created} created, {skipped} already present")


def _shared_columns(cur, table):
    cur.execute(
        """
        select column_name from information_schema.columns
        where table_schema=%s and table_name=%s
        """,
        (SIEZAL_DB, table),
    )
    siezal_cols = {r[0] for r in cur.fetchall()}
    cur.execute(
        """
        select column_name from information_schema.columns
        where table_schema=%s and table_name=%s
        """,
        (SZL_DB, table),
    )
    szl_cols = {r[0] for r in cur.fetchall()}
    shared = sorted(siezal_cols & szl_cols)
    only_siezal = siezal_cols - szl_cols
    only_szl = szl_cols - siezal_cols
    if only_siezal or only_szl:
        print(f"WARNING [{table}]: siezal-only columns {only_siezal}, szl-only columns {only_szl} -- these are skipped, not copied")
    return shared


def bulk_copy_table(table, id_column="name"):
    """Cross-database INSERT ... SELECT with an explicit, name-matched
    column list (never SELECT * -- confirmed tabItem has identical columns
    on both sites but in a different physical order). Idempotent: only
    copies rows whose id_column isn't already present on szl."""
    conn = MySQLdb.connect(host="127.0.0.1", user="root", password=_database_password(), database=SIEZAL_DB, autocommit=True)
    try:
        with conn.cursor() as cur:
            columns = _shared_columns(cur, table)
            col_list = ", ".join(f"`{c}`" for c in columns)
            query = f"""
                INSERT INTO `{SZL_DB}`.`{table}` ({col_list})
                SELECT {col_list} FROM `{SIEZAL_DB}`.`{table}` src
                WHERE NOT EXISTS (
                    SELECT 1 FROM `{SZL_DB}`.`{table}` dst
                    WHERE dst.`{id_column}` = src.`{id_column}`
                )
            """
            cur.execute(query)
            print(f"{table}: {cur.rowcount} rows copied")
    finally:
        conn.close()


def main():
    previous_in_patch = frappe.flags.in_patch
    frappe.flags.in_patch = True
    try:
        import_item_groups()
        import_brands()
    finally:
        frappe.flags.in_patch = previous_in_patch
    bulk_copy_table("tabItem", id_column="name")
    bulk_copy_table("tabItem Barcode", id_column="name")
    # Every item's own stock_uom self-conversion row ("Pcs = 1 Pcs" on every
    # item, the only UOM used site-wide -- no real alternate-UOM data exists
    # to lose here). Missing entirely after the raw-SQL Item copy above,
    # since that bypasses the Item controller logic that normally
    # auto-creates it on insert().
    bulk_copy_table("tabUOM Conversion Detail", id_column="name")
    frappe.clear_cache()
    print("Catalog replication complete.")
