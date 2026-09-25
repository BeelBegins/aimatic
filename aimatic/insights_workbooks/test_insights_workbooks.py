import unittest

from aimatic.insights_workbooks.build import BUILDERS, MANIFESTS


class TestInsightsWorkbookTemplates(unittest.TestCase):
	def test_each_folder_has_dashboard_and_kpis(self):
		for folder, builder in BUILDERS.items():
			wb = builder()
			self.assertEqual(wb["type"], "Workbook")
			deps = wb["dependencies"]
			self.assertTrue(deps["queries"])
			self.assertTrue(deps["charts"])
			dashboards = deps["dashboards"]
			self.assertGreaterEqual(len(dashboards), 1)
			items = next(iter(dashboards.values()))["items"]
			self.assertTrue(any(i.get("type") == "chart" for i in items))
			manifest = MANIFESTS[folder]
			for key in ("title", "description", "required_apps", "source_doctypes"):
				self.assertIn(key, manifest)
			self.assertNotIn("FBR", manifest["title"])
			self.assertNotIn("FBR", manifest["description"])
			self.assertNotIn("FBR", wb["doc"]["title"])
		self.assertIn("owner_flash", BUILDERS)
		owner = BUILDERS["owner_flash"]()
		self.assertEqual(owner["doc"]["title"], "CEO")
		self.assertIn("tq-owner-item-margin", owner["dependencies"]["queries"])
		self.assertIn("tc-item-margin", owner["dependencies"]["charts"])
		self.assertIn("voucher_detail_no", owner["dependencies"]["queries"]["tq-owner-item-margin"]["operations"][0]["raw_sql"])
		# Headline gross profit covers every POS sale, so it is computed in the
		# daily scorecard query, not narrowed to ledger-carrying item lines.
		daily_sql = owner["dependencies"]["queries"]["tq-owner-daily"]["operations"][0]["raw_sql"]
		self.assertIn("gross_profit", daily_sql)
		self.assertIn("si.is_pos = 1", daily_sql)
		for chart in ("tc-margin-kpis", "tc-gp"):
			self.assertEqual(owner["dependencies"]["charts"][chart]["query"], "tq-owner-daily")
		self.assertIn("units_per_ticket", daily_sql)
		self.assertIn("avg_ticket", daily_sql)
		# On-shelf availability is a Bin snapshot: no date column, so the card must
		# not claim one or the date filter would silently drop every row.
		self.assertIn("tq-owner-availability", owner["dependencies"]["queries"])
		avail = owner["dependencies"]["charts"]["tc-availability"]
		self.assertEqual(avail["chart_type"], "Number")
		self.assertNotIn("date_column", avail["config"])
		self.assertFalse(avail["config"]["comparison"])
		owner_items = next(iter(owner["dependencies"]["dashboards"].values()))["items"]
		self.assertTrue(any(i.get("filter_name") == "Branch" for i in owner_items))
		self.assertTrue(any(i.get("filter_name") == "Warehouse" for i in owner_items))
		date_links = next(i for i in owner_items if i.get("filter_name") == "Date Range")["links"]
		# Insights stacks Number-chart measures vertically on phones. Dashboard
		# tiles must therefore reference one-measure variants, not the old
		# multi-metric tile that clips labels and overlaps the next chart.
		for item in owner_items:
			if item.get("type") != "chart":
				continue
			chart = owner["dependencies"]["charts"][item["chart"]]
			if chart["chart_type"] == "Number":
				self.assertEqual(len(chart["config"]["number_columns"]), 1)
		self.assertTrue(any(i.get("chart") == "tc-flash-net-sales" for i in owner_items))
		self.assertNotIn("tc-availability", date_links)
		self.assertNotIn("tc-availability-branch", date_links)
		self.assertIn("basket_relevance", BUILDERS)
		basket = BUILDERS["basket_relevance"]()
		self.assertIn("tq-product-relevance", basket["dependencies"]["queries"])
		relevance_sql = basket["dependencies"]["queries"]["tq-product-relevance"]["operations"][0]["raw_sql"]
		self.assertIn("branch_day", relevance_sql)
		self.assertIn("HAVING COUNT(DISTINCT invoice) >= 3", relevance_sql)
		self.assertIn("basket_lift", relevance_sql)
		self.assertIn("accounts_liabilities", BUILDERS)
		self.assertIn("pending_work", BUILDERS)
		self.assertIn("ABC", BUILDERS["goods_abc"]()["doc"]["title"])
		self.assertIn("Liabilit", BUILDERS["accounts_liabilities"]()["doc"]["title"])
		self.assertIn("branch_transfers", BUILDERS)
		transfers = BUILDERS["branch_transfers"]()
		self.assertEqual(transfers["doc"]["title"], "Branch Stock Transfers")
		queries = transfers["dependencies"]["queries"]
		transfer_sql = queries["tq-branch-transfers"]["operations"][0]["raw_sql"]
		self.assertIn("se.purpose = 'Material Transfer'", transfer_sql)
		self.assertIn("wf.custom_branch != wt.custom_branch", transfer_sql)
		plan_sql = queries["tq-branch-rebalancing"]["operations"][0]["raw_sql"]
		# Joining derived CTEs to each other made MariaDB run >5 minutes on live
		# data; positions are merged with UNION ALL + GROUP BY instead.
		self.assertIn("UNION ALL", plan_sql)
		self.assertNotIn("LEFT JOIN sales", plan_sql)
		self.assertNotRegex(plan_sql, r"(?m)^keys AS")
