(function () {
	"use strict";

	var storageKey = "examic-soft-theme";
	var allowedThemes = ["sage", "sky", "lilac"];

	function isLmsHost() {
		var host = window.location.hostname;
		return host === "lms.aimatic.tech" || host === "examic.study" || host === "www.examic.study";
	}

	function polishLogin() {
		if (!/\/login\/?$/.test(window.location.pathname)) return;
		document.querySelectorAll(".page-card-head h4").forEach(function (heading) {
			if (/create a\s+examic/i.test(heading.textContent)) {
				heading.textContent = "Create your student account";
			}
		});
		document.querySelectorAll(".page-card-head .app-logo").forEach(function (logo) {
			logo.alt = "Examic Study";
			logo.src = "/assets/aimaticlearning/images/examic-study-mark.svg?v=20260902-6";
		});
	}

	function selectedTheme() {
		var saved = window.localStorage.getItem(storageKey);
		return allowedThemes.indexOf(saved) !== -1 ? saved : "sage";
	}

	function applyTheme(theme) {
		if (allowedThemes.indexOf(theme) === -1) theme = "sage";
		document.documentElement.setAttribute("data-theme", "light");
		document.documentElement.setAttribute("data-examic-soft-theme", theme);
		window.localStorage.setItem(storageKey, theme);
	}

	function addPicker() {
		if (!/^\/lms\/courses\//.test(window.location.pathname)) return;
		var rail = document.querySelector(".aimatic-lms-chapter-rail");
		if (!rail || rail.querySelector(".aimatic-theme-picker")) return;
		var picker = document.createElement("label");
		picker.className = "aimatic-theme-picker";
		picker.innerHTML = "<span>Theme</span><select aria-label=\"Choose a soft colour theme\"><option value=\"sage\">Soft Sage</option><option value=\"sky\">Soft Sky</option><option value=\"lilac\">Soft Lilac</option></select>";
		var select = picker.querySelector("select");
		select.value = selectedTheme();
		select.addEventListener("change", function () { applyTheme(select.value); });
		rail.querySelector("[class~='bg-surface-gray-1']")?.appendChild(picker) || rail.prepend(picker);
	}

	function boot() {
		if (!isLmsHost()) return;
		applyTheme(selectedTheme());
		addPicker();
		polishLogin();
	}

	function apply() {
		if (!isLmsHost()) return;
		applyTheme(selectedTheme());
		addPicker();
		polishLogin();
	}

	const observer = window.AimaticLmsDom && window.AimaticLmsDom.observe(apply);
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			boot();
			if (observer) observer.run();
		});
	} else {
		boot();
		if (observer) observer.run();
	}
})();
