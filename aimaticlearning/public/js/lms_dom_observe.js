(function (window) {
	"use strict";

	function observe(callback) {
		let scheduled = false;
		let mutating = false;

		function run() {
			if (mutating) return;
			mutating = true;
			try {
				callback();
			} finally {
				mutating = false;
			}
		}

		function schedule() {
			if (scheduled || mutating) return;
			scheduled = true;
			requestAnimationFrame(function () {
				scheduled = false;
				run();
			});
		}

		if (document.body) {
			new MutationObserver(schedule).observe(document.body, {
				childList: true,
				subtree: true,
			});
		}

		return { run: run, schedule: schedule };
	}

	window.AimaticLmsDom = { observe: observe };
})(window);
