"""Versioned retail-finance capability registry.

The registry is intentionally code-owned. A capability must be added or have its
version increased whenever its setup contract changes, so future work cannot
silently disappear from the implementation plan.
"""

from copy import deepcopy

REGISTRY_VERSION = "1.0.0"


def _capability(
	capability_id,
	label,
	category,
	implementation_status,
	description,
	guidance,
	*,
	version="1.0.0",
	phase="Foundation",
	critical=False,
	check_key=None,
	route=None,
):
	return {
		"id": capability_id,
		"label": label,
		"version": version,
		"category": category,
		"phase": phase,
		"implementation_status": implementation_status,
		"critical": critical,
		"description": description,
		"guidance": guidance,
		"check_key": check_key,
		"route": route,
	}


_CAPABILITIES = (
	_capability("capability_governance", "Capability governance", "Foundation", "implemented", "A versioned register keeps working, partial, and missing finance capabilities explicit.", "Increase a capability version whenever its setup contract, calculation, or evidence changes.", check_key="company"),
	_capability("company_foundation", "Company and chart of accounts", "Foundation", "standard", "ERPNext company, currency, chart of accounts, and control accounts form the accounting base.", "Complete Company defaults and use active leaf accounts for posting.", critical=True, check_key="company", route="List/Company"),
	_capability("store_accounting_dimension", "Store accounting dimension", "Foundation", "implemented", "Each store is represented by Branch with a matched cost center and operational warehouses.", "Create the Branch dimension, then map every store to one leaf cost center and its leaf warehouses.", critical=True, check_key="stores", route="List/Branch"),
	_capability("pos_cashier_controls", "POS and cashier controls", "Operations", "implemented", "POS Profiles carry the store, warehouse, and cost-center context used by cashier transactions.", "Every enabled POS Profile must have a Branch, matching leaf warehouse, and matching leaf cost center.", critical=True, check_key="pos", route="List/POS Profile"),
	_capability("opening_balance_cutover", "Opening-balance cutover", "Foundation", "partial", "Supplier, inventory, and accounting openings are the accepted cutover baseline; operations and reporting continue forward from it.", "Do not reconstruct unavailable history or alter current iPOS migration entries. Build a guided opening wizard only for future site onboarding.", check_key="company", route="List/Journal Entry"),
	_capability("store_profit_and_loss", "Store-wise profit and loss", "Management Reporting", "implemented", "Branch-tagged GL entries support store-level income and expense reporting.", "Keep all operational income and expense postings branch-tagged and reconcile exceptions.", check_key="reporting", route="query-report/Profit and Loss Statement"),
	_capability("consolidated_profit_and_loss", "Consolidated profit and loss", "Management Reporting", "standard", "ERPNext company and consolidated financial statements support combined P&L reporting when the organization structure is configured.", "Configure parent/group companies only when more legal entities are introduced.", check_key="company", route="query-report/Profit and Loss Statement"),
	_capability("store_balance_sheet", "Store-wise balance sheet", "Management Reporting", "separate", "A controlled allocation policy is required before assets and liabilities can be reliably presented by store.", "Define balance-sheet attribution and shared-account allocation before implementation.", phase="Finance Phase 2"),
	_capability("store_cash_position", "Store-wise cash position", "Treasury", "partial", "Cash and bank balances exist, but store attribution depends on account and dimension discipline.", "Map store cash accounts or enforce Branch on cash movements before certifying this view.", phase="Finance Phase 2", check_key="cash_bank", route="query-report/Cash Flow"),
	_capability("daily_sales_deposit_reconciliation", "Daily sales vs bank deposits", "Treasury", "separate", "Daily tender totals must be matched to deposit batches and bank transactions.", "Add deposit batches, expected-versus-deposited variance, ageing, and sign-off.", phase="Finance Phase 2"),
	_capability("inventory_valuation", "Inventory valuation", "Inventory", "standard", "ERPNext Stock Ledger and valuation settings provide forward inventory value.", "Maintain stock reconciliation and never infer unavailable pre-cutover movement history.", check_key="inventory", route="query-report/Stock Balance"),
	_capability("category_gross_profit", "Gross profit by category", "Management Reporting", "partial", "Item-group sales and valuation data are available, but finance certification and exception reconciliation remain required.", "Reconcile Sales Invoice, Stock Ledger, returns, and landed costs before certifying margin.", phase="Finance Phase 2", check_key="inventory", route="query-report/Gross Profit"),
	_capability("supplier_payables", "Supplier payables", "Working Capital", "standard", "ERPNext payable ledgers and ageing provide supplier outstanding balances.", "Reconcile supplier opening balances and future invoices/payments to the payable control account.", check_key="receivables_payables", route="query-report/Accounts Payable"),
	_capability("customer_receivables", "Customer receivables (B2B)", "Working Capital", "standard", "ERPNext receivable ledgers and ageing provide customer outstanding balances.", "Use credit customers and reconcile invoices, receipts, and opening balances to the receivable control account.", check_key="receivables_payables", route="query-report/Accounts Receivable"),
	_capability("bank_reconciliation", "Bank reconciliation", "Treasury", "standard", "ERPNext Bank Transaction and Bank Reconciliation provide statement matching.", "Import bank statements and complete matching at an agreed daily or weekly cadence.", check_key="cash_bank", route="List/Bank Transaction"),
	_capability("petty_cash", "Petty cash management", "Treasury", "separate", "Store petty-cash issue, expense evidence, replenishment, and surprise counts need a guided control workflow.", "Define float limits, custodians, vouchers, approvals, and count variance handling.", phase="Finance Phase 2"),
	_capability("inter_store_accounting", "Inter-store accounting", "Operations", "partial", "Stock transfers are supported; transfer-in-transit, expense, and due-to/due-from reconciliation need control reporting.", "Reconcile every transfer and any financial cross-charge between stores.", phase="Finance Phase 2", check_key="stores", route="List/Stock Entry"),
	_capability("branch_expenses", "Branch-wise expense tracking", "Management Reporting", "implemented", "Branch and cost-center dimensions support store expense attribution.", "Require Branch and the mapped cost center on store-originated expense lines.", check_key="reporting", route="query-report/Profit and Loss Statement"),
	_capability("head_office_allocation", "Head-office cost allocation", "Management Reporting", "separate", "Shared costs require versioned allocation drivers and auditable journals.", "Agree drivers such as revenue, area, headcount, or fixed percentages before implementation.", phase="Finance Phase 2"),
	_capability("budget_vs_actual", "Budget vs actual", "Planning", "standard", "ERPNext budgets support cost-center and accounting-dimension controls.", "Enter approved budgets by fiscal year and ownership level before relying on variance reporting.", check_key="reporting", route="List/Budget"),
	_capability("consolidated_financial_statements", "Consolidated financial statements", "Management Reporting", "standard", "ERPNext supports consolidated statements across configured company trees.", "This becomes testable when additional legal entities or group companies exist.", check_key="company", route="query-report/Consolidated Financial Statement"),
	_capability("store_gross_margin", "Store-wise gross margin percentage", "Management Reporting", "partial", "Branch-tagged sales and valuation support a draft margin view.", "Certify only after stock, returns, landed-cost, and GL reconciliation.", phase="Finance Phase 2", check_key="reporting", route="query-report/Gross Profit"),
	_capability("inventory_shrinkage", "Inventory shrinkage", "Inventory", "separate", "Physical counts, wastage, expiry, theft, and approved adjustments must be classified separately.", "Add reason codes, approval thresholds, count cycles, and financial-impact reporting.", phase="Finance Phase 2"),
	_capability("void_refund_impact", "Financial impact of voids and refunds", "Controls", "partial", "Returns and authorization logs exist, but one reconciled financial-impact dashboard is still required.", "Join void/refund events to stock, tender, tax, and GL reversals and expose exceptions.", phase="Finance Phase 2", route="List/POS Admin Audit Log"),
	_capability("supplier_rebates", "Supplier rebates and credit notes", "Procurement", "separate", "Rebate agreements, accruals, claims, credit notes, and settlement matching need dedicated controls.", "Define agreement types and accounting policy before implementation.", phase="Finance Phase 3"),
	_capability("branch_ebitda", "Branch-wise EBITDA", "Management Reporting", "separate", "Certified branch EBITDA depends on gross margin, direct expenses, and head-office allocations.", "Implement only after the margin and allocation policies are approved.", phase="Finance Phase 3"),
	_capability("daily_cash_bank", "Daily cash and bank position", "Treasury", "partial", "ERPNext cash/bank ledgers provide balances; a daily operational position and close sign-off remain to be built.", "Combine cashier closing, unbanked cash, deposits in transit, and bank balances.", phase="Finance Phase 2", check_key="cash_bank", route="query-report/Cash Flow"),
	_capability("tax_compliance", "VAT and sales-tax compliance", "Compliance", "partial", "FBR configuration and tax ledgers exist, but filing-period reconciliation and evidence dashboards remain separate work.", "Reconcile taxable sales, returns, exemptions, output tax, submissions, and payment before filing.", phase="Finance Phase 2", check_key="tax", route="List/FBR Integration Settings"),
	_capability("stock_gl_reconciliation", "Stock to GL reconciliation", "Controls", "separate", "Inventory value must reconcile to stock-in-hand accounts with explained timing differences.", "Add period-close reconciliation with drill-down and sign-off.", phase="Finance Phase 2"),
	_capability("pos_gl_reconciliation", "POS to GL reconciliation", "Controls", "separate", "POS sales, tenders, taxes, returns, and closing totals must reconcile to accounting entries.", "Add daily completeness and amount checks with cashier/store drill-down.", phase="Finance Phase 2"),
	_capability("subledger_control_reconciliation", "Subledger to control-account reconciliation", "Controls", "separate", "Receivable and payable ageing totals must match their GL control accounts.", "Add period-close exceptions and sign-off for customers and suppliers.", phase="Finance Phase 2"),
)


def get_capabilities():
	"""Return a defensive copy suitable for JSON serialization."""
	return deepcopy(list(_CAPABILITIES))
