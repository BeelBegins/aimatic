(function () {
	"use strict";

	function polishPublishedChapterHubs() {
		document.querySelectorAll(".aimatic-chapter-hub").forEach(function (root) {
			root.querySelectorAll(".ach-section-label").forEach(function (label, index) {
				if (label.textContent.trim().toLowerCase() === "continue reading") {
					label.textContent = "Part " + String(index + 1).padStart(2, "0");
				}
			});
		});
	}

	const observer = window.AimaticLmsDom && window.AimaticLmsDom.observe(polishPublishedChapterHubs);
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			polishPublishedChapterHubs();
			if (observer) observer.run();
		});
	} else {
		polishPublishedChapterHubs();
		if (observer) observer.run();
	}
})();
