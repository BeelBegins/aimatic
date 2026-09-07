(function () {
	"use strict";

	function isLmsRoute() {
		return /^\/lms(?:\/|$)/.test(window.location.pathname);
	}

	function currentLessonContext() {
		const course = window.location.pathname.match(/^\/lms\/courses\/([^/]+)\/learn\/(\d+)-(\d+)/);
		if (!course) return null;
		return { course: course[1], chapter: Number(course[2]), lesson: Number(course[3]) };
	}

	function appendStudyBuddyMessage(host, kind, text, sourceLabel) {
		host.hidden = false;
		const message = document.createElement("section");
		message.className = "aimatic-study-buddy-message aimatic-study-buddy-message-" + kind;
		const label = document.createElement("strong");
		label.textContent = kind === "user" ? "You" : "Study Buddy";
		const body = document.createElement("p");
		body.textContent = text;
		message.append(label, body);
		if (sourceLabel) {
			const source = document.createElement("small");
			source.textContent = "Source: " + sourceLabel;
			message.append(source);
		}
		host.append(message);
		host.scrollTop = host.scrollHeight;
	}

	function wireStudyBuddy(rail) {
		const card = rail.querySelector("[data-study-buddy]");
		if (!card) return;
		const heading = document.querySelector(".aimatic-lms-lesson-main h1");
		const topic = card.querySelector("[data-study-buddy-topic]");
		if (heading && topic) topic.textContent = heading.textContent.trim();
		if (card.dataset.studyBuddyReady) return;
		card.dataset.studyBuddyReady = "1";
		card.dataset.studyBuddyHistory = "[]";
		const input = card.querySelector("textarea");
		const form = card.querySelector("form");
		const submit = form.querySelector("button[type=submit]");
		const status = card.querySelector("[data-study-buddy-status]");
		const transcript = card.querySelector("[data-study-buddy-transcript]");
		card.querySelectorAll("[data-study-buddy-prompt]").forEach(function (button) {
			button.addEventListener("click", function () {
				input.value = button.dataset.studyBuddyPrompt;
				input.focus();
			});
		});
		form.addEventListener("submit", function (event) {
			event.preventDefault();
			const question = input.value.trim();
			const context = currentLessonContext();
			if (!question) {
				input.focus();
				return;
			}
			if (!context) {
				status.textContent = "Open a lesson before asking Study Buddy.";
				return;
			}
			let history = [];
			try { history = JSON.parse(card.dataset.studyBuddyHistory || "[]"); } catch (_) {}
			appendStudyBuddyMessage(transcript, "user", question);
			input.value = "";
			submit.disabled = true;
			status.textContent = "Checking the approved material for this lesson…";
			fetch("/api/method/aimaticlearning.lms_learning.study_buddy.ask_study_buddy", {
				method: "POST",
				credentials: "same-origin",
				headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token || "" },
				body: JSON.stringify({
					course: context.course,
					chapter: context.chapter,
					lesson: context.lesson,
					question: question,
					history: JSON.stringify(history),
				}),
			})
				.then(function (response) {
					if (!response.ok) throw new Error("Study Buddy is temporarily unavailable.");
					return response.json();
				})
				.then(function (payload) {
					const result = payload.message || {};
					if (!result.answer) throw new Error("Study Buddy could not complete that answer.");
					appendStudyBuddyMessage(transcript, "assistant", result.answer, result.source && result.source.label);
					history.push({ role: "user", content: question }, { role: "assistant", content: result.answer });
					card.dataset.studyBuddyHistory = JSON.stringify(history.slice(-6));
					status.textContent = result.notice || "Source-grounded response for the selected lesson.";
				})
				.catch(function (error) {
					status.textContent = error.message || "Study Buddy is temporarily unavailable. Please try again.";
				})
				.finally(function () { submit.disabled = false; });
		});
	}

	function addAiRail(grid) {
		const existing = grid.querySelector(".aimatic-lms-ai-rail");
		if (existing) {
			wireStudyBuddy(existing);
			return;
		}

		const rail = document.createElement("aside");
		rail.className = "aimatic-lms-ai-rail";
		rail.setAttribute("aria-label", "Study Buddy AI");
		rail.innerHTML =
			'<section class="aimatic-lms-ai-card aimatic-study-buddy" data-study-buddy>' +
				'<div class="aimatic-lms-ai-kicker"><span class="aimatic-lms-ai-spark">✦</span> Study Buddy AI <b>Source-grounded beta</b></div>' +
				'<div class="aimatic-lms-ai-context"><span>Grounded in approved material</span><strong data-study-buddy-topic>This lesson</strong><small>Lesson context is selected automatically.</small></div>' +
				'<h2>Ask about this lesson.</h2>' +
				'<p>Designed to explain, test and focus revision using the selected lesson—not generic prompts or copied text.</p>' +
				'<div class="aimatic-study-buddy-transcript" data-study-buddy-transcript aria-live="polite" hidden></div>' +
				'<div class="aimatic-study-buddy-prompts" aria-label="Suggested questions">' +
					'<button type="button" data-study-buddy-prompt="Explain the key rule in simple terms.">Explain the key rule</button>' +
					'<button type="button" data-study-buddy-prompt="Test me on the most important points in this lesson.">Test my recall</button>' +
					'<button type="button" data-study-buddy-prompt="What should I remember for SQE-style questions?">Focus my revision</button>' +
				'</div>' +
				'<form class="aimatic-study-buddy-form"><label class="sr-only" for="aimatic-study-buddy-question">Ask Study Buddy</label><textarea id="aimatic-study-buddy-question" rows="3" maxlength="1200" placeholder="Ask a focused question about this lesson"></textarea><button type="submit">Ask Study Buddy <span aria-hidden="true">↗</span></button></form>' +
				'<p class="aimatic-study-buddy-status" data-study-buddy-status>Your question and this lesson&rsquo;s approved text are sent to Study Buddy for a source-grounded response. Not legal advice.</p>' +
			'</section>';
		grid.appendChild(rail);
		wireStudyBuddy(rail);
	}

	function decorateLessonPage() {
		if (!isLmsRoute()) return;
		document.body.classList.add("aimatic-lms-learner");

		const aside = Array.from(document.querySelectorAll("aside")).find(function (node) {
			return node.querySelector("ul") && node.querySelector("a, button");
		});
		if (!aside) return;

		const grid = aside.parentElement;
		if (!grid || grid.children.length < 2) return;
		if (!grid.classList.contains("aimatic-lms-lesson-grid")) {
			grid.classList.add("aimatic-lms-lesson-grid");
			Array.from(grid.children).forEach(function (child) {
				if (child === aside) {
					child.classList.add("aimatic-lms-chapter-rail");
				} else if (!child.classList.contains("aimatic-lms-ai-rail")) {
					child.classList.add("aimatic-lms-lesson-main");
				}
			});
		}
		addAiRail(grid);
	}
	function initChapterHub(root) {
		if (!root || root.dataset.aimaticHubReady) return;
		root.dataset.aimaticHubReady = "1";

		const tabs = root.querySelectorAll(".ach-tab");
		const panels = root.querySelectorAll(".ach-panel[data-ach-panel]");
		tabs.forEach(function (tab) {
			tab.addEventListener("click", function () {
				const name = tab.dataset.achTab;
				tabs.forEach(function (item) {
					const active = item === tab;
					item.classList.toggle("is-active", active);
					item.setAttribute("aria-selected", active ? "true" : "false");
				});
				panels.forEach(function (panel) {
					const active = panel.dataset.achPanel === name;
					panel.classList.toggle("is-active", active);
					panel.hidden = !active;
				});
			});
		});

		const study = root.querySelector("[data-ach-flash-study]");
		if (!study) return;
		const items = Array.from(study.querySelectorAll("[data-ach-card]"));
		const ratings = study.querySelectorAll("[data-ach-rating]");
		let current = items.findIndex(function (item) { return !item.hidden; });
		if (current < 0) current = 0;

		function flip(item) {
			const front = item.querySelector(".ach-card-front");
			const back = item.querySelector(".ach-card-back");
			const card = item.querySelector("[data-ach-flash-card]");
			const revealed = !back.hidden;
			front.hidden = revealed;
			back.hidden = !revealed;
			card.classList.toggle("is-back", revealed);
			ratings.forEach(function (button) { button.disabled = !revealed; });
		}

		function showNext() {
			items[current].hidden = true;
			current += 1;
			if (current >= items.length) {
				study.innerHTML = '<div class="ach-flash-complete"><strong>Deck complete</strong><span>Revise Hard cards from Revision, or study this deck again.</span><p><a href="/learning-revision?course=business-law-practice-blp">Open weak areas</a></p></div>';
				return;
			}
			items[current].hidden = false;
			const card = items[current].querySelector("[data-ach-flash-card]");
			const front = items[current].querySelector(".ach-card-front");
			const back = items[current].querySelector(".ach-card-back");
			front.hidden = false;
			back.hidden = true;
			card.classList.remove("is-back");
			ratings.forEach(function (button) { button.disabled = true; });
		}

		items.forEach(function (item) {
			const card = item.querySelector("[data-ach-flash-card]");
			card.addEventListener("click", function () { flip(item); });
			card.addEventListener("keydown", function (event) {
				if (event.key === "Enter" || event.key === " ") {
					event.preventDefault();
					flip(item);
				}
			});
		});

		ratings.forEach(function (button) {
			button.disabled = true;
			button.addEventListener("click", function () {
				const item = items[current];
				if (!item || button.disabled) return;
				button.disabled = true;
				frappe.call({
					method: "aimaticlearning.lms_learning.api.review_flashcard",
					args: { name: item.dataset.achCardName, rating: button.dataset.achRating },
					callback: showNext,
					errorback: function () { button.disabled = false; },
				});
			});
		});
	}

	function bootHubs() {
		document.querySelectorAll(".aimatic-chapter-hub").forEach(initChapterHub);
	}

	function boot() {
		if (!isLmsRoute()) return;
		decorateLessonPage();
		setTimeout(decorateLessonPage, 250);
		setTimeout(decorateLessonPage, 800);
		bootHubs();
		setTimeout(decorateLessonPage, 1800);
	}

	function apply() {
		if (!isLmsRoute()) return;
		decorateLessonPage();
		bootHubs();
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
