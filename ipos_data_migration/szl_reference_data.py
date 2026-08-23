"""Reference data for setting up `szl` under the same Company as production `siezal`.

Every value here was captured by direct inspection of siezal's live database
(Company "Siezal Super Market", abbr SSM) on 2026-07-27, not computed or
guessed. This is the one reviewable "what are we about to create on szl"
artifact for the whole szl multi-branch setup pass -- see setup_szl.md for the
run order and rationale. Plain data only, no frappe import, no side effects.
"""

COMPANY_NAME = "Siezal Super Market"
COMPANY_ABBR = "SSM"

FISCAL_YEAR = {
	"name": "2026-2027",
	"year_start_date": "2026-07-01",
	"year_end_date": "2027-06-30",
}

# Accounts confirmed present on siezal but not auto-created by ERPNext's own
# "Standard with Numbers" template Company-creation path on a from-scratch
# site (confirmed live: szl's freshly-created tree was missing all of these).
# Most have no account_number (added by hand, never templated); "Bank
# Account" is the one exception -- it does have a template-assigned number
# (1201) but isn't actually inserted by create_charts()/create_default_
# accounts() itself on this Frappe version, only by the interactive Setup
# Wizard bank-account step, which szl never ran.
NON_TEMPLATE_ACCOUNTS = [
	{
		"account_name": "Bank Account",
		"root_type": "Asset",
		"parent_account_name": "Bank Accounts",
		"account_type": "Bank",
		"account_number": "1201",
	},
	{
		"account_name": "Advance Tax Deducted by Suppliers",
		"root_type": "Asset",
		"parent_account_name": "Tax Assets",
		"account_type": "Tax",
	},
	{
		"account_name": "GST",
		"root_type": "Liability",
		"parent_account_name": "Duties and Taxes",
		"account_type": "Tax",
	},
	{
		"account_name": "Withholding Tax Payable",
		"root_type": "Liability",
		"parent_account_name": "Duties and Taxes",
		"account_type": "Tax",
	},
	{
		"account_name": "Fbr Pos Service Fee",
		"root_type": "Liability",
		"parent_account_name": "Duties and Taxes",
		"account_type": "Liability",
	},
]

# The 42 leaf accounts siezal has under "Indirect Expenses" (verified via a
# direct query against siezal's own tabAccount, not transcribed from a
# summary -- an earlier summarized count of "41" was off by one). Numbers and
# account_type ("" = none set) match siezal exactly, including the
# out-of-order append gaps (5242 does not exist, never did).
INDIRECT_EXPENSE_PARENT_NAME = "Indirect Expenses"
INDIRECT_EXPENSE_LEAVES = [
	("Administrative Expenses", "5201", ""),
	("Commission on Sales", "5202", ""),
	("Depreciation", "5203", "Depreciation"),
	("Entertainment Expenses", "5204", ""),
	("Freight and Forwarding Charges", "5205", "Chargeable"),
	("Legal Expenses", "5206", ""),
	("Marketing Expenses", "5207", "Chargeable"),
	("Office R&M Expenses", "5208", "Indirect Expense"),
	("Office Rent", "5209", ""),
	("Postal Expenses", "5210", ""),
	("Print and Stationery", "5211", ""),
	("Round Off", "5212", "Round Off"),
	("Salary", "5213", ""),
	("Sales Expenses", "5214", ""),
	("Telephone Expenses", "5215", ""),
	("Travel Expenses", "5216", ""),
	("Office Electricity Expenses", "5217", "Indirect Expense"),
	("Write Off", "5218", ""),
	("Exchange Gain/Loss", "5219", ""),
	("Interest Expense", "5220", ""),
	("Bank Charges", "5221", ""),
	("Gain/Loss on Asset Disposal", "5222", ""),
	("Miscellaneous Expenses", "5223", "Chargeable"),
	("Impairment", "5224", ""),
	("Tax Expense", "5225", ""),
	("Gift Vouchers Discount Expense", "5226", ""),
	("Vehicle Fuel Expense", "5227", "Indirect Expense"),
	("Bike Fuel Expense", "5228", "Indirect Expense"),
	("DG Fuel Expenses", "5229", "Indirect Expense"),
	("Vehicle R&M Expenses", "5230", "Indirect Expense"),
	("Bike R&M Expenses", "5231", "Indirect Expense"),
	("DG R&M Expenses", "5232", "Indirect Expense"),
	("Stores R&M Expenses", "5233", "Indirect Expense"),
	("Store Electricity Expense", "5234", "Indirect Expense"),
	("Residence Electricity Expenses", "5235", "Indirect Expense"),
	("Store Rent", "5236", "Indirect Expense"),
	("Residence Rent", "5237", "Indirect Expense"),
	("Packing Shoppers", "5238", "Indirect Expense"),
	("Meezan Credit Card Machine Charges", "5239", "Indirect Expense"),
	("Govt Departments Fee", "5240", "Indirect Expense"),
	("Insurance", "5241", "Indirect Expense"),
	("Fine & Penalties", "5243", "Indirect Expense"),
]

# Company default-account fields, resolved by account_name lookup at runtime
# (never assume a full "<n> - <name> - SSM" string) rather than hardcoded
# full names, so this stays correct regardless of exactly which accounts the
# standard template itself produced vs. what this script added.
COMPANY_DEFAULTS = {
	"default_receivable_account": "Debtors",
	"default_payable_account": "Creditors",
	"write_off_account": "Write Off",
	"default_expense_account": "Cost of Goods Sold",
	"default_income_account": "Sales",
	"round_off_account": "Round Off",
	"exchange_gain_loss_account": "Exchange Gain/Loss",
	"accumulated_depreciation_account": "Accumulated Depreciation",
	"depreciation_expense_account": "Depreciation",
	"disposal_account": "Gain/Loss on Asset Disposal",
	"capital_work_in_progress_account": "CWIP Account",
	"asset_received_but_not_billed": "Asset Received But Not Billed",
	"default_inventory_account": "Stock In Hand",
	"stock_adjustment_account": "Stock Adjustment",
	"stock_received_but_not_billed": "Stock Received But Not Billed",
	"default_employee_advance_account": "Employee Advances",
	"default_payroll_payable_account": "Payroll Payable",
}
# default_bank_account / default_cash_account are deliberately left unset,
# matching siezal's own actual (blank) values.

HEAD_OFFICE_COST_CENTER_NAME = "Head Office"

# The 5 core Mode of Payment masters szl is missing (only "Gift Voucher"
# exists there so far, from an earlier patch). enabled flags match siezal's
# actual live state exactly.
MODE_OF_PAYMENT_MASTERS = [
	{"name": "Cash", "type": "Cash", "enabled": 0},
	{"name": "Bank Draft", "type": "Bank", "enabled": 0},
	{"name": "Cheque", "type": "Bank", "enabled": 1},
	{"name": "Credit Card", "type": "Bank", "enabled": 1},
	{"name": "Wire Transfer", "type": "Bank", "enabled": 1},
]

# Tax Withholding Category rates (Tax Withholding Group is just "Filers"/
# "Non-Filers", created via the same fbrtype-mapping helper import_siezal_
# suppliers.py already uses).
TAX_WITHHOLDING_RATES = [0, 0.25, 0.5, 1, 2.5, 5, 5.5]
WHT_RATE_FROM_DATE = "2020-01-01"
WHT_RATE_TO_DATE = "2030-12-31"

# The 5 new branches. Branch = Cost Center = Warehouse base name (one string
# reused across all three, confirmed with the user -- simpler than siezal's
# own inconsistent S1 naming, where Branch "Ghouri Town VIP" and Warehouse
# "Ghouri Town Phase V" are different strings). store_code/ledger_suffix are
# locked in now for later use (Mode of Payment, POS terminal IDs) even though
# this pass doesn't create POS Profiles yet.
BRANCHES = [
	{
		# S1 (Ghouri Town VIP) is the only branch actually selling right now --
		# on the old iPOS software, not on any ERPNext site. siezal is a
		# test/sandbox deployment only; szl is the real production site, so
		# S1 gets a real branch here too rather than staying cross-site (see
		# setup_szl.md's "Internal Branch Supplier" note -- that construct
		# remains for siezal's own test use, not real production once S1 has
		# a genuine branch here). Reuses "S1GT" as ledger_suffix, matching
		# what siezal's own legacy accounts already call this store.
		"store_code": "S1",
		"branch_name": "S1 - Ghouri Town VIP",
		"ledger_suffix": "S1GT",
	},
	{
		"store_code": "S2",
		"branch_name": "S2 - Bhatta Chowk",
		"ledger_suffix": "S2BC",
	},
	{
		"store_code": "S4",
		"branch_name": "S4 - Wallayat Complex",
		"ledger_suffix": "S4WC",
	},
	{
		"store_code": "S5",
		"branch_name": "S5 - Sector C",
		"ledger_suffix": "S5SC",
	},
	{
		"store_code": "S6",
		"branch_name": "S6 - Khalid Block",
		"ledger_suffix": "S6KB",
	},
	{
		"store_code": "S7",
		"branch_name": "S7 - Empire Heights",
		"ledger_suffix": "S7EH",
	},
]

# Phase C -- Internal Branch Supplier / Inter-Branch Payable mechanism.
INTER_BRANCH_GROUP_ACCOUNT_NUMBER = "2140"
INTER_BRANCH_GROUP_ACCOUNT_NAME = "Inter-Branch Accounts"
INTER_BRANCH_GROUP_PARENT_NAME = "Current Liabilities"
# One leaf per new branch, numbered sequentially from the group number.
INTER_BRANCH_PAYABLE_NUMBERS = {
	"S2": "2141",
	"S4": "2142",
	"S5": "2143",
	"S6": "2144",
	"S7": "2145",
}
INTERNAL_BRANCH_SUPPLIER_NAME = "Internal Branch - S1 (Ghouri Town VIP)"
INTERNAL_BRANCH_SUPPLIER_GROUP = "Internal"
INTERNAL_BRANCH_SUPPLIER_DESCRIPTION = (
	"Phased-rollout accounting construct only. Represents S1 (Ghouri Town VIP), "
	"which lives on the separate 'siezal' Frappe site -- a real ERPNext Material "
	"Transfer is not possible across two separate site databases, so stock/money "
	"movement between S1 and any of this site's branches (S2/S4/S5/S6/S7) is "
	"recorded as a Purchase Receipt/Invoice against this Supplier instead. Do "
	"not apply purchase tax or Withholding Tax to these transactions, and do "
	"not use the normal Trade Creditors account -- always set `credit_to` "
	"explicitly to the relevant branch's own 'Inter-Branch Payable - <code> - "
	"SSM' account (see setup_szl.md). Discontinue this construct once/if "
	"cross-site tooling or consolidation makes a real Material Transfer "
	"possible, reconciling any outstanding balance to a real Due To/Due From "
	"account at that time."
)
