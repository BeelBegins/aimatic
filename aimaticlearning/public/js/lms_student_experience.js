(function () {
	"use strict";

	const SQE1_PATHWAYS = [
		{
			key: "FLK1",
			label: "SQE1 · FLK1",
			subjects: [
				["business-law-practice-blp", "Business Law & Practice"],
				["dispute-resolution", "Dispute Resolution"],
				["contract-law", "Contract Law"],
				["tort-law", "Tort Law"],
				["public-law", "Legal System, Public Law & EU Law"],
				["legal-services", "Legal Services"],
			],
		},
		{
			key: "FLK2",
			label: "SQE1 · FLK2",
			subjects: [
				["criminal-law", "Criminal Law"],
				["criminal-litigation", "Criminal Litigation"],
				["equity-and-trust-law", "Equity & Trust Law"],
				["land-law", "Land Law"],
				["property-practice", "Property Practice"],
				["solicitors-accounts", "Solicitors' Accounts"],
				["wills-and-administration-of-estates", "Wills & Administration of Estates"],
			],
		},
	];

	const SQE1_SUBJECTS = SQE1_PATHWAYS.reduce(function (all, pathway) {
		return all.concat(pathway.subjects);
	}, []);


	function courseIdFromLink(link) {
		const match = new URL(link.href, window.location.origin).pathname.match(/^\/lms\/courses\/([^/]+)/);
		return match && SQE1_SUBJECTS.some(function (subject) { return subject[0] === match[1]; }) ? match[1] : null;
	}

	function addSqePathwayCatalogue() {
		if (!/^\/lms\/courses\/?$/.test(window.location.pathname)) return;
		const links = Array.from(document.querySelectorAll('a[href*="/lms/courses/"]')).filter(function (link) {
			return Boolean(courseIdFromLink(link));
		});
		if (!links.length) return;
		const sourceGrid = links[0].closest(".grid");
		if (!sourceGrid) return;
		const mapped = links.map(function (link) { return { link: link, course: courseIdFromLink(link) }; });
		if (mapped.some(function (item) { return !item.course; })) return;
		const host = sourceGrid.parentElement;
		const signature = mapped.map(function (item) { return item.course; }).join(",");
		const existing = host.querySelector(".aimatic-sqe-pathway");
		if (existing && existing.dataset.signature === signature) return;
		if (existing) existing.remove();
		sourceGrid.style.display = "none";
		sourceGrid.setAttribute("aria-hidden", "true");
		const title = document.querySelector("h1");
		if (title) title.textContent = "SQE1 preparation";

		const root = makeNode("section", "aimatic-sqe-pathway");
		root.dataset.signature = signature;
		root.setAttribute("aria-labelledby", "aimatic-sqe-pathway-title");
		const intro = makeNode("div", "aimatic-sqe-pathway-intro");
		intro.append(makeNode("span", "aimatic-sqe-kicker", "Examic Study"));
		intro.append(makeNode("h2", "", "SQE1"));
		intro.append(makeNode("p", "", "Choose a knowledge file, then open a subject. Chapters and study tools live inside the subject so the pathway stays easy to follow."));
		intro.querySelector("h2").id = "aimatic-sqe-pathway-title";
		root.append(intro);

		const available = SQE1_PATHWAYS.map(function (pathway) {
			return {
				pathway: pathway,
				items: mapped.filter(function (item) {
					return pathway.subjects.some(function (subject) { return subject[0] === item.course; });
				}),
			};
		}).filter(function (group) { return group.items.length; });
		const tabs = makeNode("div", "aimatic-sqe-pathway-tabs");
		tabs.setAttribute("role", "tablist");
		const panels = makeNode("div", "aimatic-sqe-pathway-panels");
		available.forEach(function (group, index) {
			const tabId = "aimatic-sqe-tab-" + group.pathway.key.toLowerCase();
			const panelId = "aimatic-sqe-panel-" + group.pathway.key.toLowerCase();
			const tab = makeNode("button", "aimatic-sqe-pathway-tab", group.pathway.label);
			tab.type = "button";
			tab.id = tabId;
			tab.dataset.pathway = group.pathway.key;
			tab.setAttribute("role", "tab");
			tab.setAttribute("aria-controls", panelId);
			tab.setAttribute("aria-selected", String(index === 0));
			tab.tabIndex = index === 0 ? 0 : -1;
			tabs.append(tab);

			const panel = makeNode("section", "aimatic-sqe-pathway-panel");
			panel.id = panelId;
			panel.setAttribute("role", "tabpanel");
			panel.setAttribute("aria-labelledby", tabId);
			panel.hidden = index !== 0;
			const heading = makeNode("div", "aimatic-sqe-panel-heading");
			heading.append(makeNode("h3", "", group.pathway.label));
			heading.append(makeNode("span", "", group.items.length + " subjects"));
			panel.append(heading);
			const cards = makeNode("div", "aimatic-sqe-course-grid");
			group.items.forEach(function (item) {
				const card = item.link.cloneNode(true);
				card.classList.add("aimatic-sqe-course-card");
				cards.append(card);
			});
			panel.append(cards);
			panels.append(panel);
		});
		root.append(tabs, panels);
		tabs.querySelectorAll("[role=tab]").forEach(function (tab) {
			tab.addEventListener("click", function () {
				tabs.querySelectorAll("[role=tab]").forEach(function (other) {
					const selected = other === tab;
					other.setAttribute("aria-selected", String(selected));
					other.tabIndex = selected ? 0 : -1;
				});
				panels.querySelectorAll("[role=tabpanel]").forEach(function (panel) {
					panel.hidden = panel.id === tab.getAttribute("aria-controls") ? false : true;
				});
			});
		});
		host.append(root);
	}


	function currentSqeCourse() {
		const match = window.location.pathname.match(/^\/lms\/courses\/([^/]+)(?:\/|$)/);
		return match && SQE1_SUBJECTS.some(function (subject) { return subject[0] === match[1]; }) ? match[1] : null;
	}

	function addSqeSwitcher() {
		const course = currentSqeCourse();
		const rail = document.querySelector(".aimatic-lms-chapter-rail");
		if (!course || !rail || rail.querySelector(".aimatic-sqe-switcher")) return;
		const currentPathway = SQE1_PATHWAYS.find(function (pathway) {
			return pathway.subjects.some(function (subject) { return subject[0] === course; });
		}) || SQE1_PATHWAYS[0];
		const nav = document.createElement("nav");
		nav.className = "aimatic-flk1-switcher aimatic-sqe-switcher";
		nav.setAttribute("aria-label", "SQE1 subjects");
		const groups = SQE1_PATHWAYS.map(function (pathway) {
			const items = pathway.subjects.map(function (subject) {
				const active = subject[0] === course;
				return '<a href="/lms/courses/' + subject[0] + '"' + (active ? ' aria-current="page"' : '') + '><span>' + subject[1] + '</span>' + (active ? '<b>Current</b>' : '<i aria-hidden="true">›</i>') + '</a>';
			}).join("");
			return '<div class="aimatic-sqe-switcher-group"><small>' + pathway.label + '</small>' + items + '</div>';
		}).join("");
		nav.innerHTML = '<button type="button" aria-expanded="false"><span><small>SQE1 · ' + currentPathway.key + '</small><strong>Switch subject</strong></span><i aria-hidden="true">⌄</i></button><div hidden>' + groups + '</div>';
		nav.querySelector("button").addEventListener("click", function () {
			const list = nav.querySelector("div");
			list.hidden = !list.hidden;
			this.setAttribute("aria-expanded", String(!list.hidden));
		});
		rail.insertBefore(nav, rail.firstChild);
	}


	function addRevisionEntry() {
		const course = currentSqeCourse();
		const rail = document.querySelector(".aimatic-lms-chapter-rail");
		if (!course || !rail || rail.querySelector(".aimatic-revision-entry")) return;
		const link = document.createElement("a");
		link.className = "aimatic-revision-entry";
		link.href = "/learning-revision?course=" + encodeURIComponent(course);
		link.innerHTML = "<small>Your progress</small><strong>Revision · weak areas</strong>";
		const switcher = rail.querySelector(".aimatic-flk1-switcher");
		if (switcher && switcher.nextSibling) rail.insertBefore(link, switcher.nextSibling);
		else if (switcher) rail.appendChild(link);
		else rail.insertBefore(link, rail.firstChild);
	}

	function isBlpLesson() {
		return /^\/lms\/courses\/business-law-practice-blp(?:\/|$)/.test(window.location.pathname);
	}

	function polishStudyBuddy() {
		if (!currentSqeCourse()) return;
		const rail = document.querySelector(".aimatic-lms-ai-rail");
		const heading = document.querySelector(".aimatic-lms-lesson-main h1");
		const topic = rail && rail.querySelector("[data-study-buddy-topic]");
		if (topic && heading) topic.textContent = heading.textContent.trim();
	}

	function hidePoweredByBranding() {
		document
			.querySelectorAll("span.lucide-zap.size-4.text-ink-gray-7.cursor-pointer")
			.forEach(function (icon) {
				icon.style.setProperty("display", "none", "important");
				icon.setAttribute("aria-hidden", "true");
			});
	}

	function hideInstructorByline() {
		if (!isBlpLesson()) return;
		document.querySelectorAll('.aimatic-lms-lesson-main a[href*="/lms/user/"]').forEach(function (link) {
			let row = link.parentElement;
			while (row && row.parentElement && !row.classList.contains("aimatic-lms-lesson-main")) {
				if (row.classList.contains("flex") && row.classList.contains("items-center")) break;
				row = row.parentElement;
			}
			if (row) row.classList.add("aimatic-lms-instructor-byline");
		});
	}

	function makeNode(tag, className, textValue) {
		const node = document.createElement(tag);
		if (className) node.className = className;
		if (textValue !== undefined) node.textContent = textValue;
		return node;
	}

	function requestedRating(host) {
		const fromHost = host && host.dataset.ratingFilter;
		if (fromHost === "all") return "";
		if (fromHost) return fromHost;
		const fromQuery = (new URLSearchParams(window.location.search).get("rating") || "").toLowerCase();
		const fromHash = (window.location.hash || "").replace(/^#revise-/, "").toLowerCase();
		const value = fromQuery || fromHash;
		return ["hard", "good", "easy", "unreviewed"].includes(value) ? value : "";
	}

	function ratingLabel(rating) {
		if (rating === "unreviewed") return "unreviewed";
		return rating ? rating[0].toUpperCase() + rating.slice(1) : "";
	}

	function renderFlashcardDeck(host, cards) {
		delete host.dataset.aimaticFlashcardsLoading;
		const loader = host.querySelector("[data-aimatic-flashcard-loader]");
		if (loader) loader.remove();
		const rating = requestedRating(host);
		if (!cards.length) {
			const empty = makeNode("div", "aimatic-flashcard-state aimatic-flashcard-empty");
			empty.append(makeNode("strong", "", rating
				? "No " + ratingLabel(rating) + " flashcards in this deck yet."
				: "No flashcards are published for this chapter yet."));
			if (rating) {
				empty.append(makeNode("span", "", "Rate cards Hard, Good or Easy first, or study the full deck."));
				const all = makeNode("button", "ach-rate", "Study all cards");
				all.type = "button";
				all.addEventListener("click", function () { reloadFlashcardDeck(host, ""); });
				empty.append(all);
			}
			host.append(empty);
			host.dataset.aimaticFlashcardsReady = "1";
			return;
		}

		const study = makeNode("div", "ach-flash-study");
		study.setAttribute("data-ach-flash-study", "");
		const filter = makeNode("div", "ach-flash-filter");
		filter.append(makeNode("span", "", rating ? "Revising " + ratingLabel(rating) + " cards" : "Recall rating"));
		filter.append(makeNode("span", "ach-flash-filter-hint", "Choose after revealing"));
		study.append(filter);

		cards.forEach(function (cardData, index) {
			const card = makeNode("article", "ach-flash-item");
			card.setAttribute("data-ach-card", "");
			card.dataset.achCardName = cardData.name || "";
			card.dataset.achDifficulty = cardData.difficulty || "Medium";
			card.hidden = index !== 0;

			const flip = makeNode("div", "ach-flash-card");
			flip.setAttribute("data-ach-flash-card", "");
			flip.setAttribute("role", "button");
			flip.setAttribute("tabindex", "0");
			flip.setAttribute("aria-label", "Flashcard. Select to reveal the answer.");
			flip.append(makeNode("span", "ach-card-face ach-card-front", cardData.front || ""));
			const back = makeNode("span", "ach-card-face ach-card-back", cardData.back || "");
			back.hidden = true;
			flip.append(back);
			flip.append(makeNode("span", "ach-flip-label", "Select to reveal"));
			card.append(flip);

			const meta = makeNode("div", "ach-flash-card-meta");
			meta.append(makeNode("span", "", cardData.difficulty || "Medium"));
			meta.append(makeNode("span", "", "Card " + (index + 1) + " of " + cards.length));
			card.append(meta);
			study.append(card);
		});

		const actions = makeNode("div", "ach-flash-actions");
		["hard", "good", "easy"].forEach(function (rating) {
			const label = rating[0].toUpperCase() + rating.slice(1);
			const button = makeNode("button", "ach-rate ach-rate-" + rating, label);
			button.type = "button";
			button.disabled = true;
			button.dataset.achRating = rating;
			actions.append(button);
		});
		study.append(actions);
		host.append(study);
		host.dataset.aimaticFlashcardsReady = "1";
	}

	function showFlashcardError(host) {
		const loader = host.querySelector("[data-aimatic-flashcard-loader]");
		if (!loader) return;
		loader.classList.add("is-error");
		loader.replaceChildren(
			makeNode("strong", "", "Flashcards could not be loaded."),
			makeNode("span", "", "Refresh the page or sign in again.")
		);
	}

	function reloadFlashcardDeck(host, rating) {
		host.dataset.ratingFilter = rating || "all";
		delete host.dataset.aimaticFlashcardsReady;
		delete host.dataset.aimaticFlashcardsLoading;
		delete host.dataset.examicSessionReady;
		host.querySelectorAll("[data-ach-flash-study], .aimatic-flashcard-state, .examic-flash-intro").forEach(function (node) {
			node.remove();
		});
		if (!host.querySelector("[data-aimatic-flashcard-loader]")) {
			const loader = makeNode("div", "aimatic-flashcard-loader");
			loader.setAttribute("data-aimatic-flashcard-loader", "");
			loader.append(makeNode("strong", "", rating ? "Loading " + ratingLabel(rating) + " cards…" : "Loading flashcards…"));
			host.append(loader);
		}
		loadFlashcardDeck(host);
	}

	function loadFlashcardDeck(host) {
		if (host.dataset.aimaticFlashcardsLoading || host.dataset.aimaticFlashcardsReady) return;
		host.dataset.aimaticFlashcardsLoading = "1";
		if (!host.dataset.ratingFilter) host.dataset.ratingFilter = requestedRating(host) || "all";
		const params = new URLSearchParams({
			learning_module: host.dataset.learningModule || "",
			course_chapter: host.dataset.courseChapter || "",
			limit: "200",
		});
		if (host.dataset.ratingFilter && host.dataset.ratingFilter !== "all") {
			params.set("rating_filter", host.dataset.ratingFilter);
		}
		fetch("/api/method/aimaticlearning.lms_learning.api.get_flashcard_deck?" + params.toString(), {
			credentials: "same-origin",
		})
			.then(function (response) {
				if (!response.ok) throw new Error("Flashcards could not be loaded.");
				return response.json();
			})
			.then(function (payload) {
				renderFlashcardDeck(host, (payload.message && payload.message.cards) || []);
			})
			.catch(function () {
				delete host.dataset.aimaticFlashcardsLoading;
				showFlashcardError(host);
			});
	}

	function loadFlashcardDecks() {
		if (!isBlpLesson()) return;
		document.querySelectorAll("[data-aimatic-flashcard-deck]").forEach(loadFlashcardDeck);
	}

	function nextFlashcard(study, current) {
		const cards = Array.from(study.querySelectorAll("[data-ach-card]"));
		const nextIndex = cards.indexOf(current) + 1;
		current.hidden = true;
		if (nextIndex >= cards.length) {
			const course = currentSqeCourse() || "business-law-practice-blp";
			const host = study.closest("[data-aimatic-flashcard-deck]");
			study.innerHTML = '<div class="ach-flash-complete"><strong>Deck complete</strong><span>Revise Hard cards now, or open your weak-area board.</span><div class="ach-flash-complete-actions"><button type="button" data-ach-revise="hard">Revise Hard</button><button type="button" data-ach-revise="good">Revise Good</button><button type="button" data-ach-revise="easy">Revise Easy</button><a href="/learning-revision?course=' + encodeURIComponent(course) + '">Weak areas</a></div></div>';
			if (host) {
				study.querySelectorAll("[data-ach-revise]").forEach(function (button) {
					button.addEventListener("click", function () { reloadFlashcardDeck(host, button.dataset.achRevise); });
				});
			}
			return;
		}
		const next = cards[nextIndex];
		next.hidden = false;
		next.querySelector(".ach-card-front").hidden = false;
		next.querySelector(".ach-card-back").hidden = true;
		const flip = next.querySelector("[data-ach-flash-card]");
		flip.classList.remove("is-back");
		flip.setAttribute("aria-label", "Flashcard. Select to reveal the answer.");
		flip.querySelector(".ach-flip-label").textContent = "Select to reveal";
		study.querySelectorAll("[data-ach-rating]").forEach(function (button) {
			button.disabled = true;
		});
	}

	function saveFlashcardReview(button) {
		const study = button.closest("[data-ach-flash-study]");
		const current = study && Array.from(study.querySelectorAll("[data-ach-card]")).find(function (item) {
			return !item.hidden;
		});
		if (!study || !current || button.disabled) return;
		button.disabled = true;
		fetch("/api/method/aimaticlearning.lms_learning.api.review_flashcard", {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				"X-Frappe-CSRF-Token": window.csrf_token || "",
			},
			body: JSON.stringify({
				name: current.dataset.achCardName,
				rating: button.dataset.achRating,
			}),
		})
			.then(function (response) {
				if (!response.ok) throw new Error("Flashcard review could not be saved.");
				nextFlashcard(study, current);
			})
			.catch(function () {
				button.disabled = false;
			});
	}

	window.AimaticFlashcards = {
		reload: reloadFlashcardDeck,
	};

	function polish() {
		addSqePathwayCatalogue();
		addSqeSwitcher();
		addRevisionEntry();
		polishStudyBuddy();
		hideInstructorByline();
		hidePoweredByBranding();
		loadFlashcardDecks();
	}

	document.addEventListener(
		"click",
		function (event) {
			if (!isBlpLesson()) return;
			const flashcard = event.target.closest("[data-ach-flash-card]");
			if (flashcard) {
				event.preventDefault();
				event.stopImmediatePropagation();
				const front = flashcard.querySelector(".ach-card-front");
				const back = flashcard.querySelector(".ach-card-back");
				const label = flashcard.querySelector(".ach-flip-label");
				const revealing = back.hidden;
				front.hidden = revealing;
				back.hidden = !revealing;
				flashcard.classList.toggle("is-back", revealing);
				flashcard.setAttribute("aria-label", revealing ? "Flashcard answer shown." : "Flashcard. Select to reveal the answer.");
				if (label) label.textContent = revealing ? "Answer revealed" : "Select to reveal";
				const study = flashcard.closest("[data-ach-flash-study]");
				if (study) study.querySelectorAll("[data-ach-rating]").forEach(function (rating) {
					rating.disabled = !revealing;
				});
				return;
			}
			const button = event.target.closest("[data-ach-rating]");
			if (!button) return;
			event.preventDefault();
			event.stopImmediatePropagation();
			saveFlashcardReview(button);
		},
		true
	);

	document.addEventListener("keydown", function (event) {
		if (!isBlpLesson() || (event.key !== "Enter" && event.key !== " ")) return;
		const flashcard = event.target.closest("[data-ach-flash-card]");
		if (!flashcard) return;
		event.preventDefault();
		flashcard.click();
	});

	const observer = window.AimaticLmsDom && window.AimaticLmsDom.observe(polish);
	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			if (observer) observer.run();
			else polish();
		});
	} else if (observer) {
		observer.run();
	} else {
		polish();
	}
})();
