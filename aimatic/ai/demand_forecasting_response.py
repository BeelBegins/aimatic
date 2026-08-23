"""Structured response components for certified demand forecasts."""

from frappe.utils import flt

from aimatic.ai.response_schema import (
	KPI,
	Chart,
	ChartData,
	ChartOptions,
	DrillDown,
	Pagination,
	Table,
	TableColumn,
)


def _forecast_rows(result: dict) -> list[dict]:
	rows = []
	for forecast in result.get("forecasts") or []:
		row = {
			key: value
			for key, value in forecast.items()
			if key not in {"history", "forecast", "stock_plan", "candidate_model_scores"}
		}
		row.update(forecast.get("stock_plan") or {})
		rows.append(row)
	return rows


def forecast_kpis(result: dict) -> list[KPI]:
	rows = result.get("forecasts") or []
	if not rows:
		return []
	reorder_quantity = sum(
		flt((row.get("stock_plan") or {}).get("suggested_reorder_quantity")) for row in rows
	)
	return [
		KPI(
			key="forecast_total_quantity",
			label="Forecast Demand",
			value=round(sum(flt(row.get("forecast_quantity")) for row in rows), 4),
			format="qty",
		),
		KPI(
			key="forecast_average_confidence",
			label="Average Forecast Confidence",
			value=round(sum(flt(row.get("forecast_confidence")) for row in rows) / len(rows), 2),
			format="percent",
		),
		KPI(
			key="forecast_low_confidence",
			label="Low-Confidence Forecasts",
			value=sum(row.get("confidence_label") == "low" for row in rows),
			format="number",
			severity="warning" if any(row.get("confidence_label") == "low" for row in rows) else None,
		),
		KPI(
			key="forecast_reorder_quantity",
			label="Suggested Reorder Quantity",
			value=round(reorder_quantity, 4),
			format="qty",
			severity="watch" if reorder_quantity > 0 else None,
		),
	]


def forecast_table(result: dict) -> Table | None:
	rows = _forecast_rows(result)
	if not rows:
		return None
	return Table(
		id="table_demand_forecast",
		title="Demand Forecast and Stock Plan",
		columns=[
			TableColumn(key="item_code", label="Item", type="link", doctype="Item"),
			TableColumn(key="item_name", label="Item Name", type="text"),
			TableColumn(key="branch", label="Branch", type="link", doctype="Branch"),
			TableColumn(key="warehouse", label="Warehouse", type="link", doctype="Warehouse"),
			TableColumn(key="selected_model", label="Selected Model", type="text"),
			TableColumn(key="forecast_period", label="Forecast Period", type="text"),
			TableColumn(key="forecast_quantity", label="Forecast Qty", type="qty"),
			TableColumn(key="lower_confidence_bound", label="Lower Bound", type="qty"),
			TableColumn(key="upper_confidence_bound", label="Upper Bound", type="qty"),
			TableColumn(key="historical_average", label="Historical Avg", type="qty"),
			TableColumn(key="recent_trend", label="Recent Trend", type="qty"),
			TableColumn(key="wape", label="WAPE %", type="percent"),
			TableColumn(key="mae", label="MAE", type="float"),
			TableColumn(key="bias", label="Bias", type="float"),
			TableColumn(key="historical_periods", label="History Periods", type="int"),
			TableColumn(key="zero_demand_periods", label="Zero Periods", type="int"),
			TableColumn(key="forecast_confidence", label="Confidence", type="percent"),
			TableColumn(key="available_stock", label="Available Stock", type="qty"),
			TableColumn(key="incoming_stock", label="Incoming Stock", type="qty"),
			TableColumn(key="safety_stock", label="Safety Stock", type="qty"),
			TableColumn(key="reorder_point", label="Reorder Point", type="qty"),
			TableColumn(key="stockout_risk_date", label="Stockout Risk Date", type="date"),
			TableColumn(key="suggested_reorder_quantity", label="Suggested Reorder", type="qty"),
			TableColumn(key="expected_ending_stock", label="Expected Ending Stock", type="qty"),
		],
		rows=rows,
		pagination=Pagination(page=1, page_size=len(rows), total=len(rows)),
		drill_down=DrillDown(
			target="POS Invoice",
			param_map={"item_code": "item_code", "warehouse": "warehouse"},
		),
		metadata={
			"source": result.get("source"),
			"date_from": result.get("date_from"),
			"date_to": result.get("date_to"),
			"calculation_version": result.get("calculation_version"),
		},
	)


def forecast_charts(result: dict) -> list[Chart]:
	charts = []
	for index, row in enumerate((result.get("forecasts") or [])[:3]):
		history = row.get("history") or []
		forecast = row.get("forecast") or []
		if not history and not forecast:
			continue
		labels = [entry["period"] for entry in history] + [entry["period"] for entry in forecast]
		historical_values = [flt(entry["quantity"]) for entry in history] + [None] * len(forecast)
		if history and forecast:
			bridge = flt(history[-1]["quantity"])
			forecast_values = (
				[None] * (len(history) - 1)
				+ [bridge]
				+ [flt(entry["forecast_quantity"]) for entry in forecast]
			)
			lower_values = [None] * len(history) + [
				flt(entry["lower_confidence_bound"]) for entry in forecast
			]
			upper_values = [None] * len(history) + [
				flt(entry["upper_confidence_bound"]) for entry in forecast
			]
		else:
			forecast_values = [flt(entry["forecast_quantity"]) for entry in forecast]
			lower_values = [flt(entry["lower_confidence_bound"]) for entry in forecast]
			upper_values = [flt(entry["upper_confidence_bound"]) for entry in forecast]
		charts.append(
			Chart(
				id=f"chart_demand_forecast_{index + 1}",
				title=f"Demand Forecast — {row.get('item_name') or row.get('item_code')}",
				type="line",
				data=ChartData(
					labels=labels,
					datasets=[
						{"label": "Historical Demand", "data": historical_values},
						{"label": "Forecast", "data": forecast_values},
						{"label": "Lower Bound", "data": lower_values},
						{"label": "Upper Bound", "data": upper_values},
					],
				),
				options=ChartOptions(yAxis={"format": "qty"}),
				auto_selected=True,
			)
		)
	return charts


KPI_DISPATCH = {"get_demand_forecast": forecast_kpis}
TABLE_DISPATCH = {"get_demand_forecast": forecast_table}
CHARTS_DISPATCH = {"get_demand_forecast": forecast_charts}
