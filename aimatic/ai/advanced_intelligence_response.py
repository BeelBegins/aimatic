"""Structured output builders for certified advanced retail intelligence."""

from frappe.utils import flt

from aimatic.ai.response_schema import Chart, ChartData, ChartOptions, KPI, Pagination, Table, TableColumn


def _rows(result, key):
	return result.get(key) or []


def _transfer_kpis(result):
	rows = _rows(result, "recommendations")
	return [
		KPI("transfer_count", "Recommended Transfers", len(rows), "number"),
		KPI("transfer_quantity", "Transfer Quantity", sum(flt(r.get("transfer_qty")) for r in rows), "qty"),
		KPI("transfer_dead_stock_reduction", "Expected Dead-Stock Reduction", sum(flt(r.get("expected_dead_stock_reduction")) for r in rows), "currency"),
	] if rows else []


def _promotion_kpis(result):
	kpis = [
		KPI("promo_incremental_units", "Incremental Units", flt(result.get("incremental_units")), "qty"),
		KPI("promo_incremental_revenue", "Incremental Revenue", flt(result.get("incremental_revenue")), "currency"),
	]
	if result.get("incremental_margin") is not None:
		kpis.append(KPI("promo_incremental_margin", "Incremental Margin", flt(result.get("incremental_margin")), "currency"))
	if result.get("promotion_roi_pct") is not None:
		kpis.append(KPI("promo_roi", "Promotion ROI", flt(result.get("promotion_roi_pct")), "percent"))
	return kpis


def _rfm_kpis(result):
	rows = _rows(result, "segments")
	return [
		KPI("rfm_customers", "Customers Segmented", len(rows), "number"),
		KPI("rfm_high_value", "High-Value Customers", len(result.get("high_value_customers") or []), "number"),
		KPI("rfm_dormant", "Dormant Customers", len(result.get("dormant_customers") or []), "number", severity="warning"),
	] if rows else []


def _basket_kpis(result):
	rows = _rows(result, "pairs")
	return [
		KPI("basket_transactions", "Transactions Analyzed", flt(result.get("transaction_count")), "number"),
		KPI("basket_pairs", "Qualified Item Pairs", len(rows), "number"),
		KPI("basket_best_lift", "Highest Lift", max((flt(r.get("lift")) for r in rows), default=0), "number"),
	]


def _vendor_kpis(result):
	rows = _rows(result, "vendors")
	return [
		KPI("vendor_count", "Vendors Analyzed", len(rows), "number"),
		KPI("vendor_purchase_volume", "Purchase Volume", sum(flt(r.get("purchase_volume")) for r in rows), "currency"),
		KPI("vendor_avg_reliability", "Average Reliability", sum(flt(r.get("reliability_score")) for r in rows) / len(rows), "percent"),
	] if rows else []


def _anomaly_kpis(result):
	rows = _rows(result, "anomalies")
	return [
		KPI("anomaly_count", "Anomalies Detected", len(rows), "number", severity="warning" if rows else None),
		KPI("anomaly_critical", "Critical Anomalies", sum(r.get("severity") == "critical" for r in rows), "number", severity="critical"),
	]


def _table(table_id, title, rows, columns, result):
	if not rows:
		return None
	return Table(
		id=table_id,
		title=title,
		columns=[TableColumn(**column) for column in columns],
		rows=rows,
		pagination=Pagination(page=1, page_size=len(rows), total=int(result.get("row_count") or len(rows))),
		metadata={"calculation_version": result.get("calculation_version")},
	)


def _transfer_table(result):
	return _table("table_branch_transfers", "Branch Transfer Recommendations", _rows(result, "recommendations"), [
		{"key": "item_code", "label": "Item", "type": "link", "doctype": "Item"},
		{"key": "from_branch", "label": "From Branch", "type": "link", "doctype": "Branch"},
		{"key": "to_branch", "label": "To Branch", "type": "link", "doctype": "Branch"},
		{"key": "transfer_qty", "label": "Transfer Qty", "type": "qty"},
		{"key": "expected_avoided_stockout_days", "label": "Avoided Stockout Days", "type": "float"},
		{"key": "expected_dead_stock_reduction", "label": "Dead-Stock Reduction", "type": "currency"},
		{"key": "transfer_confidence", "label": "Confidence", "type": "percent"},
	], result)


def _promotion_table(result):
	return _table("table_promotion_effect", "Promotion Effectiveness", [result], [
		{"key": "item_code", "label": "Item", "type": "link", "doctype": "Item"},
		{"key": "baseline_sales", "label": "Baseline Sales", "type": "currency"},
		{"key": "promotional_sales", "label": "Promotional Sales", "type": "currency"},
		{"key": "incremental_units", "label": "Incremental Units", "type": "qty"},
		{"key": "incremental_revenue", "label": "Incremental Revenue", "type": "currency"},
		{"key": "incremental_margin", "label": "Incremental Margin", "type": "currency"},
		{"key": "cannibalization", "label": "Cannibalization", "type": "currency"},
		{"key": "post_promotion_drop_pct", "label": "Post-Promo Change %", "type": "percent"},
		{"key": "promotion_roi_pct", "label": "ROI %", "type": "percent"},
	], result)


def _rfm_table(result):
	return _table("table_customer_rfm", "Customer RFM Segments", _rows(result, "segments"), [
		{"key": "customer", "label": "Customer", "type": "link", "doctype": "Customer"},
		{"key": "customer_name", "label": "Customer Name", "type": "text"},
		{"key": "recency_days", "label": "Recency Days", "type": "int"},
		{"key": "frequency", "label": "Frequency", "type": "int"},
		{"key": "monetary_value", "label": "Monetary Value", "type": "currency"},
		{"key": "rfm_score", "label": "RFM Score", "type": "text"},
		{"key": "customer_segment", "label": "Segment", "type": "text"},
		{"key": "churn_risk", "label": "Churn Risk", "type": "text"},
		{"key": "recommended_engagement_category", "label": "Engagement", "type": "text"},
	], result)


def _basket_table(result):
	return _table("table_market_basket", "Market-Basket Pairs", _rows(result, "pairs"), [
		{"key": "item_a", "label": "Item A", "type": "link", "doctype": "Item"},
		{"key": "item_b", "label": "Item B", "type": "link", "doctype": "Item"},
		{"key": "joint_transactions", "label": "Joint Transactions", "type": "int"},
		{"key": "support", "label": "Support %", "type": "percent"},
		{"key": "confidence", "label": "Confidence %", "type": "percent"},
		{"key": "lift", "label": "Lift", "type": "float"},
	], result)


def _vendor_table(result):
	return _table("table_vendor_reliability", "Vendor Reliability", _rows(result, "vendors"), [
		{"key": "supplier", "label": "Supplier", "type": "link", "doctype": "Supplier"},
		{"key": "purchase_volume", "label": "Purchase Volume", "type": "currency"},
		{"key": "price_trend_pct", "label": "Price Trend %", "type": "percent"},
		{"key": "average_lead_time_days", "label": "Avg Lead Time", "type": "float"},
		{"key": "lead_time_stddev", "label": "Lead-Time Std Dev", "type": "float"},
		{"key": "fill_rate_pct", "label": "Fill Rate %", "type": "percent"},
		{"key": "on_time_delivery_pct", "label": "On-Time %", "type": "percent"},
		{"key": "rejection_rate_pct", "label": "Rejection %", "type": "percent"},
		{"key": "vendor_concentration_risk_pct", "label": "Concentration %", "type": "percent"},
		{"key": "reliability_score", "label": "Reliability", "type": "percent"},
	], result)


def _anomaly_table(result):
	return _table("table_business_anomalies", "Business Anomalies", _rows(result, "anomalies"), [
		{"key": "severity", "label": "Severity", "type": "text"},
		{"key": "metric", "label": "Metric", "type": "text"},
		{"key": "branch", "label": "Branch", "type": "link", "doctype": "Branch"},
		{"key": "item_code", "label": "Item", "type": "link", "doctype": "Item"},
		{"key": "actual_value", "label": "Actual", "type": "float"},
		{"key": "expected_low", "label": "Expected Low", "type": "float"},
		{"key": "expected_high", "label": "Expected High", "type": "float"},
		{"key": "variance", "label": "Variance", "type": "float"},
		{"key": "data_source", "label": "Source", "type": "text"},
	], result)


def _bar_chart(chart_id, title, labels, data, label, fmt="number"):
	if not labels:
		return []
	return [Chart(chart_id, title, "bar", ChartData(labels, [{"label": label, "data": data}]), ChartOptions(horizontal=True, yAxis={"format": fmt}), True)]


def _transfer_charts(r):
	rows = _rows(r, "recommendations")[:15]
	return _bar_chart("chart_transfers", "Recommended Transfer Quantities", [f"{x.get('from_branch')} → {x.get('to_branch')}" for x in rows], [flt(x.get("transfer_qty")) for x in rows], "Quantity", "qty")


def _promotion_charts(r):
	return _bar_chart("chart_promotion", "Baseline vs Promotion", ["Baseline", "Promotion"], [flt(r.get("baseline_sales")), flt(r.get("promotional_sales"))], "Sales", "currency")


def _rfm_charts(r):
	counts = r.get("segment_counts") or {}
	return [Chart("chart_rfm", "Customer Segments", "donut", ChartData(list(counts), [{"label": "Customers", "data": list(counts.values())}]), ChartOptions(), True)] if counts else []


def _basket_charts(r):
	rows = _rows(r, "pairs")[:15]
	return _bar_chart("chart_baskets", "Strongest Basket Lift", [f"{x.get('item_a')} + {x.get('item_b')}" for x in rows], [flt(x.get("lift")) for x in rows], "Lift")


def _vendor_charts(r):
	rows = _rows(r, "vendors")[:15]
	return _bar_chart("chart_vendors", "Vendor Reliability", [x.get("supplier") for x in rows], [flt(x.get("reliability_score")) for x in rows], "Reliability", "percent")


def _anomaly_charts(r):
	rows = _rows(r, "anomalies")[:15]
	return _bar_chart("chart_anomalies", "Anomaly Magnitude", [f"{x.get('branch') or x.get('warehouse')} — {x.get('metric')}" for x in rows], [abs(flt(x.get("z_score") or x.get("variance"))) for x in rows], "Magnitude")


KPI_DISPATCH = {
	"get_branch_transfer_recommendations": _transfer_kpis,
	"get_promotion_effectiveness": _promotion_kpis,
	"get_customer_rfm_segments": _rfm_kpis,
	"get_market_basket_analysis": _basket_kpis,
	"get_vendor_reliability": _vendor_kpis,
	"get_business_anomalies": _anomaly_kpis,
}
TABLE_DISPATCH = {
	"get_branch_transfer_recommendations": _transfer_table,
	"get_promotion_effectiveness": _promotion_table,
	"get_customer_rfm_segments": _rfm_table,
	"get_market_basket_analysis": _basket_table,
	"get_vendor_reliability": _vendor_table,
	"get_business_anomalies": _anomaly_table,
}
CHARTS_DISPATCH = {
	"get_branch_transfer_recommendations": _transfer_charts,
	"get_promotion_effectiveness": _promotion_charts,
	"get_customer_rfm_segments": _rfm_charts,
	"get_market_basket_analysis": _basket_charts,
	"get_vendor_reliability": _vendor_charts,
	"get_business_anomalies": _anomaly_charts,
}
