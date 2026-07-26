"""Structured cards, chart, and table for governed price recommendations."""

from frappe.utils import flt

from aimatic.ai.response_schema import Chart, ChartData, ChartOptions, KPI, Table, TableColumn


def price_kpis(result: dict) -> list[KPI]:
	scenarios = result.get("scenarios") or []
	recommended = next((row for row in scenarios if row.get("name") == "Recommended"), None)
	if not recommended:
		return []
	currency = result.get("currency")
	kpis = [
		KPI(
			key="price_current",
			label="Current Price",
			value=flt(result.get("current_price")),
			format="currency",
			currency=currency,
		),
		KPI(
			key="price_recommended",
			label="Recommended Price",
			value=flt(recommended.get("suggested_price")),
			format="currency",
			currency=currency,
			comparison=flt(result.get("current_price")),
			variance_amount=round(
				flt(recommended.get("suggested_price")) - flt(result.get("current_price")), 2
			),
			variance_pct=flt(recommended.get("price_change_pct")),
			status="Decision support only",
			scenario="Recommended",
		),
		KPI(
			key="price_expected_gross_profit",
			label="Expected Gross Profit",
			value=flt(recommended.get("expected_gross_profit")),
			format="currency",
			currency=currency,
			scenario="Recommended",
		),
		KPI(
			key="price_expected_margin",
			label="Expected Gross Margin",
			value=flt(recommended.get("expected_gross_margin_pct")),
			format="percent",
			scenario="Recommended",
		),
	]
	if recommended.get("expected_gross_profit") is None:
		kpis = kpis[:2]
	return kpis


def price_table(result: dict) -> Table | None:
	rows = result.get("scenarios") or []
	if not rows:
		return None
	currency = result.get("currency")
	return Table(
		id="table_price_recommendation",
		title="Price Recommendation Scenarios — Decision Support Only",
		columns=[
			TableColumn(key="name", label="Scenario", type="text"),
			TableColumn(key="suggested_price", label="Suggested Price", type="currency", currency=currency),
			TableColumn(key="price_change_pct", label="Price Change %", type="percent"),
			TableColumn(key="expected_quantity", label="Expected Qty", type="qty"),
			TableColumn(key="expected_revenue", label="Expected Revenue", type="currency", currency=currency),
			TableColumn(key="expected_gross_profit", label="Expected Gross Profit", type="currency", currency=currency),
			TableColumn(key="expected_gross_margin_pct", label="Expected Margin %", type="percent"),
			TableColumn(key="expected_sell_through", label="Expected Sell-Through %", type="percent"),
			TableColumn(key="stock_cover_impact", label="Stock Cover", type="float"),
			TableColumn(key="confidence", label="Confidence", type="text"),
		],
		rows=rows,
		metadata={
			"notice": result.get("notice"),
			"automatic_update": False,
			"calculation_version": result.get("calculation_version"),
			"constraint_warnings": result.get("constraint_warnings"),
		},
	)


def price_charts(result: dict) -> list[Chart]:
	rows = result.get("scenarios") or []
	if not rows:
		return []
	currency = result.get("currency")
	return [
		Chart(
			id="chart_price_scenarios",
			title="Price Scenario Outcomes",
			type="bar",
			data=ChartData(
				labels=[row.get("name") for row in rows],
				datasets=(
					[{"label": "Expected Revenue", "data": [flt(row.get("expected_revenue")) for row in rows]}]
					+ ([{"label": "Expected Gross Profit", "data": [flt(row.get("expected_gross_profit")) for row in rows]}]
					   if any(row.get("expected_gross_profit") is not None for row in rows) else [])
				),
			),
			options=ChartOptions(yAxis={"format": "currency", "currency": currency}),
			auto_selected=True,
		)
	]


KPI_DISPATCH = {"get_price_recommendation": price_kpis}
TABLE_DISPATCH = {"get_price_recommendation": price_table}
CHARTS_DISPATCH = {"get_price_recommendation": price_charts}
