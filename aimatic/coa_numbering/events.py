import frappe


def auto_assign_account_number(doc, method=None):
    """Fill Account.account_number automatically on a new account when left
    blank, instead of leaving correct next-number-in-series bookkeeping to
    whoever is creating the account (easy to get wrong -- pick a number
    already used elsewhere in the tree, or one that collides with a future
    account someone else adds).

    Picks the smallest number not already used by any account in this
    company, bounded to the numeric "block" implied by the parent account's
    own number -- e.g. a new leaf under "5200 - Indirect Expenses" (whose
    next sibling group is "5300") gets the next free number in 5201-5299; a
    leaf under "2300 - Duties and Taxes" (next sibling group "2350") gets the
    next free number in 2301-2349. An explicit account_number the user
    already typed in is always left untouched.
    """
    if not doc.is_new() or doc.get("account_number"):
        return
    if not doc.parent_account:
        return

    parent_number = _as_int(frappe.db.get_value("Account", doc.parent_account, "account_number"))
    if parent_number is None:
        # Parent has no number, or a non-simple-integer number (e.g. a
        # rollup range like "2100-2400") -- nothing safe to base a next
        # number on.
        return

    upper_bound = _next_sibling_group_number(doc.parent_account, parent_number)
    used_numbers = _used_account_numbers(doc.company)

    for candidate in range(parent_number + 1, upper_bound):
        if candidate not in used_numbers:
            doc.account_number = str(candidate)
            return

    frappe.msgprint(
        f"Could not auto-assign an account number under '{doc.parent_account}' "
        f"(no free number between {parent_number} and {upper_bound}) -- "
        "set Account Number manually.",
        indicator="orange",
        alert=True,
    )


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _used_account_numbers(company):
    numbers = frappe.get_all("Account", filters={"company": company}, pluck="account_number")
    return {n for n in (_as_int(v) for v in numbers) if n is not None}


def _next_sibling_group_number(parent_account, parent_number, default_block=100):
    """The parent's own next sibling group (by number) bounds how far this
    block extends -- e.g. "2300 - Duties and Taxes"'s next sibling
    "2350 - Short-term Provisions" means 2300's children must stay under
    2350. Falls back to a flat +100 block if the parent has no siblings with
    a bigger number (e.g. it's the last group, or a root account)."""
    grandparent = frappe.db.get_value("Account", parent_account, "parent_account")
    if not grandparent:
        return parent_number + default_block

    sibling_numbers = frappe.get_all("Account", filters={"parent_account": grandparent}, pluck="account_number")
    bigger_siblings = sorted(n for n in (_as_int(v) for v in sibling_numbers) if n is not None and n > parent_number)
    return bigger_siblings[0] if bigger_siblings else parent_number + default_block
