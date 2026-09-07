(function () {
	function initHub(root) {
		if (!root || root.dataset.achReady) return;
		root.dataset.achReady = "1";

		const profile = root.dataset.profile;
		const module = root.dataset.module;
		const courseChapter = root.dataset.chapter;

		const tabs = root.querySelectorAll(".ach-tab");
		const panels = root.querySelectorAll(".ach-panel");

		tabs.forEach((tab) => {
			tab.addEventListener("click", () => {
				const name = tab.dataset.achTab;
				tabs.forEach((t) => {
					t.classList.toggle("is-active", t === tab);
					t.setAttribute("aria-selected", t === tab ? "true" : "false");
				});
				panels.forEach((panel) => {
					const active = panel.dataset.achPanel === name;
					panel.classList.toggle("is-active", active);
					panel.hidden = !active;
				});
			});
		});

		initNotes(root, profile);
		initFlashcards(root, module, courseChapter);
	}

	function initNotes(root, profile) {
		const body = root.querySelector("[data-ach-notes-body]");
		const moreBtn = root.querySelector("[data-ach-notes-more]");
		const progress = root.querySelector("[data-ach-notes-progress]");
		let paragraphs = [];
		let shown = 0;
		const chunk = 3;

		function renderChunk() {
			const slice = paragraphs.slice(shown, shown + chunk);
			slice.forEach((text) => {
				const p = document.createElement("p");
				p.textContent = text;
				body.appendChild(p);
			});
			shown += slice.length;
			const left = paragraphs.length - shown;
			progress.textContent =
				paragraphs.length
					? "Shown " + Math.min(shown, paragraphs.length) + " of " + paragraphs.length + " sections"
					: "";
			if (left > 0) {
				moreBtn.hidden = false;
				moreBtn.textContent = "Show more (" + Math.min(chunk, left) + ")";
			} else {
				moreBtn.hidden = true;
			}
		}

		moreBtn.addEventListener("click", () => renderChunk());

		frappe.call({
			method: "aimaticlearning.lms_learning.api.get_chapter_notes_chunks",
			args: { chapter_profile: profile },
			callback: (r) => {
				paragraphs = (r.message && r.message.paragraphs) || [];
				if (!paragraphs.length) {
					body.innerHTML = "<p>No study notes for this chapter yet.</p>";
					return;
				}
				renderChunk();
			},
		});
	}

	function initFlashcards(root, module, courseChapter) {
		const loading = root.querySelector("[data-ach-flash-loading]");
		const empty = root.querySelector("[data-ach-flash-empty]");
		const deck = root.querySelector("[data-ach-flash-deck]");
		const cardEl = root.querySelector("[data-ach-flash-card]");
		const progress = root.querySelector("[data-ach-flash-progress]");
		const concept = root.querySelector("[data-ach-flash-concept]");
		const nextBtn = root.querySelector("[data-ach-flash-next]");

		let cards = [];
		let index = 0;
		let showingBack = false;

		function showCard() {
			const card = cards[index];
			if (!card) return;
			showingBack = false;
			cardEl.textContent = card.front;
			cardEl.classList.remove("is-back");
			progress.textContent = "Card " + (index + 1) + " of " + cards.length;
			concept.textContent = card.concept || "";
		}

		cardEl.addEventListener("click", () => {
			if (!cards.length) return;
			showingBack = !showingBack;
			const card = cards[index];
			cardEl.textContent = showingBack ? card.back : card.front;
			cardEl.classList.toggle("is-back", showingBack);
		});

		nextBtn.addEventListener("click", () => {
			if (index < cards.length - 1) {
				index += 1;
				showCard();
			}
		});

		root.querySelectorAll("[data-ach-rating]").forEach((btn) => {
			btn.addEventListener("click", () => {
				const card = cards[index];
				if (!card) return;
				frappe.call({
					method: "aimaticlearning.lms_learning.api.review_flashcard",
					args: { name: card.name, rating: btn.dataset.achRating },
					callback: () => {
						if (index < cards.length - 1) {
							index += 1;
							showCard();
						}
					},
				});
			});
		});

		frappe.call({
			method: "aimaticlearning.lms_learning.api.get_flashcard_deck",
			args: {
				learning_module: module,
				course_chapter: courseChapter || undefined,
				limit: 50,
			},
			callback: (r) => {
				loading.hidden = true;
				cards = (r.message && r.message.cards) || [];
				if (!cards.length) {
					empty.hidden = false;
					return;
				}
				deck.hidden = false;
				showCard();
			},
		});
	}

	function boot() {
		document.querySelectorAll(".aimatic-chapter-hub").forEach(initHub);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", boot);
	} else {
		boot();
	}

	// LMS Vue may mount lesson content after DOMContentLoaded
	setTimeout(boot, 500);
	setTimeout(boot, 1500);
	setTimeout(boot, 3000);
})();
