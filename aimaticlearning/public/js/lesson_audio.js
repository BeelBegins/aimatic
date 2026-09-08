(function () {
	"use strict";

	function isLmsRoute() {
		return /^\/lms(?:\/|$)/.test(window.location.pathname);
	}

	function currentLessonContext() {
		const match = window.location.pathname.match(/^\/lms\/courses\/([^/]+)\/learn\/(\d+)-(\d+)/);
		if (!match) return null;
		return { course: match[1], chapter: Number(match[2]), lesson: Number(match[3]) };
	}

	function contextKey(context) {
		return context.course + "-" + context.chapter + "-" + context.lesson;
	}

	function buildPlayer(chunks) {
		const player = document.createElement("section");
		player.className = "aimatic-lesson-audio-player";
		player.setAttribute("aria-label", "Listen to this lesson");
		player.innerHTML =
			'<div class="aimatic-lesson-audio-head">' +
				'<span class="aimatic-lesson-audio-icon" aria-hidden="true">♪</span>' +
				'<span class="aimatic-lesson-audio-label">Listen to this lesson</span>' +
				'<label class="aimatic-lesson-audio-speed-label">Speed ' +
					'<select class="aimatic-lesson-audio-speed">' +
						'<option value="0.85">0.85×</option>' +
						'<option value="1" selected>1×</option>' +
						'<option value="1.15">1.15×</option>' +
						'<option value="1.35">1.35×</option>' +
					'</select>' +
				'</label>' +
			'</div>' +
			'<audio class="aimatic-lesson-audio-el" preload="none" controls></audio>' +
			'<p class="aimatic-lesson-audio-progress" aria-live="polite"></p>';

		const audio = player.querySelector(".aimatic-lesson-audio-el");
		const speed = player.querySelector(".aimatic-lesson-audio-speed");
		const progress = player.querySelector(".aimatic-lesson-audio-progress");
		let index = 0;

		function describe() {
			progress.textContent = chunks.length > 1 ? "Part " + (index + 1) + " of " + chunks.length : "";
		}

		function loadChunk(nextIndex, autoplay) {
			index = nextIndex;
			audio.src = chunks[index];
			audio.playbackRate = Number(speed.value) || 1;
			describe();
			if (autoplay) audio.play().catch(function () {});
		}

		audio.addEventListener("ended", function () {
			if (index + 1 < chunks.length) loadChunk(index + 1, true);
		});
		speed.addEventListener("change", function () {
			audio.playbackRate = Number(speed.value) || 1;
		});

		loadChunk(0, false);
		return player;
	}

	function removeExistingPlayer(main) {
		const existing = main.querySelector(".aimatic-lesson-audio-player");
		if (existing) existing.remove();
	}

	function findLessonMain() {
		const decorated = document.querySelector(".aimatic-lms-lesson-main");
		if (decorated) return decorated;
		// Mobile Lesson.vue omits the desktop chapter aside, so the learner
		// decorator cannot tag the lesson column. This stable content surface is
		// the mobile equivalent and keeps audio available on both layouts.
		return document.querySelector('[class~="bg-surface-base"][class~="min-w-0"]');
	}

	function mountPlayer(main, context) {
		const key = contextKey(context);
		if (main.dataset.lessonAudioContext === key) return;
		// Set the guard before the fetch resolves, both to avoid firing a
		// duplicate request while this one is in flight, and to drop any
		// previous lesson's player immediately on navigation rather than
		// leaving it visible until the new fetch completes.
		main.dataset.lessonAudioContext = key;
		removeExistingPlayer(main);

		fetch(
			"/api/method/aimaticlearning.lms_learning.lesson_audio.get_lesson_audio" +
				"?course=" + encodeURIComponent(context.course) +
				"&chapter=" + context.chapter +
				"&lesson=" + context.lesson,
			{ headers: { Accept: "application/json" } }
		)
			.then(function (response) { return response.ok ? response.json() : null; })
			.then(function (payload) {
				// Only mount if still on the same lesson - a fast route
				// change shouldn't drop a stale player onto the new page.
				const current = currentLessonContext();
				if (!current || contextKey(current) !== key) return;

				const chunks = payload && payload.message && payload.message.chunks;
				if (!chunks || !chunks.length) return;

				const heading = main.querySelector("h1");
				const player = buildPlayer(chunks);
				if (heading && heading.parentElement === main) {
					heading.insertAdjacentElement("afterend", player);
				} else {
					main.insertBefore(player, main.firstChild);
				}
			})
			.catch(function () {});
	}

	function apply() {
		if (!isLmsRoute()) return;
		const context = currentLessonContext();
		if (!context) return;
		const main = findLessonMain();
		if (!main) return;
		mountPlayer(main, context);
	}

	const observer = window.AimaticLmsDom && window.AimaticLmsDom.observe(apply);
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", apply);
	} else {
		apply();
	}
	if (observer) observer.run();
})();
