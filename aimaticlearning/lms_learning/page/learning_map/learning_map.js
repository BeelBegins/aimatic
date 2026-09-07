frappe.pages["learning-map"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Learning Map"),
		single_column: true,
	});

	const $root = $(`
		<div class="aimatic-learning-map" style="padding:12px;">
			<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;">
				<select class="form-control module-select" style="max-width:420px;"></select>
				<button class="btn btn-primary btn-refresh">${__("Refresh")}</button>
			</div>
			<div class="row">
				<div class="col-md-8">
					<div class="visual-map border rounded" style="min-height:360px;padding:12px;background:var(--fg-color);"></div>
				</div>
				<div class="col-md-4">
					<h5>${__("Strengths")}</h5>
					<ul class="strengths list-unstyled"></ul>
					<h5>${__("Weaknesses")}</h5>
					<ul class="weaknesses list-unstyled"></ul>
					<h5>${__("Revision plan")}</h5>
					<ul class="revisions list-unstyled text-muted"></ul>
				</div>
			</div>
		</div>
	`).appendTo(page.body);

	function loadModules() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Learning Module Config",
				fields: ["name", "title"],
				limit_page_length: 50,
			},
			callback: (r) => {
				const $select = $root.find(".module-select");
				$select.empty();
				((r.message || []).forEach((row) => {
					$select.append(`<option value="${row.name}">${row.title}</option>`);
				}));
				if ($select.val()) refresh();
			},
		});
	}

	function refresh() {
		const learning_module = $root.find(".module-select").val();
		if (!learning_module) return;
		frappe.call({
			method: "aimaticlearning.lms_learning.api.get_learning_map",
			args: { learning_module },
			callback: (r) => {
				const data = r.message || {};
				renderMap(data.visual_map || {});
				renderLists(data);
			},
		});
	}

	function renderMap(visual_map) {
		const $map = $root.find(".visual-map");
		$map.empty();
		if (!visual_map.nodes || !visual_map.nodes.length) {
			$map.text(__("Complete chapter MCQs to populate your learning map."));
			return;
		}
		visual_map.nodes.forEach((node) => {
			const color =
				node.group === "strong"
					? "#2e7d32"
					: node.group === "developing"
						? "#f9a825"
						: node.group === "needs_work"
							? "#c62828"
							: "#6c757d";
			$map.append(`
				<div style="border:1px solid var(--border-color);border-radius:8px;padding:10px;margin-bottom:8px;">
					<div style="font-weight:600;">${frappe.utils.escape_html(node.label)}</div>
					<div style="font-size:12px;color:${color};">
						${node.mastery_pct}% mastery · ${node.attempts} attempts
					</div>
				</div>
			`);
		});
	}

	function renderLists(data) {
		const $strengths = $root.find(".strengths");
		const $weaknesses = $root.find(".weaknesses");
		const $revisions = $root.find(".revisions");
		$strengths.empty();
		$weaknesses.empty();
		$revisions.empty();
		(data.strengths || []).forEach((item) => {
			$strengths.append(
				`<li>${frappe.utils.escape_html(item.concept)} (${item.mastery_pct}%)</li>`
			);
		});
		(data.weaknesses || []).forEach((item) => {
			$weaknesses.append(
				`<li>${frappe.utils.escape_html(item.concept)} (${item.mastery_pct}%)</li>`
			);
		});
		(data.revision_recommendations || []).forEach((line) => {
			$revisions.append(`<li>${frappe.utils.escape_html(line)}</li>`);
		});
	}

	$root.find(".btn-refresh").on("click", refresh);
	loadModules();
};
