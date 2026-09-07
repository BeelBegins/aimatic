frappe.ui.form.on("Course Lesson", {
	refresh(frm) {
		if (frm.is_new()) return;
		renderLessonAudioButton(frm);
	},
});

const STATUS_COLOR = {
	Ready: "green",
	Generating: "orange",
	Queued: "orange",
	Failed: "red",
	"Not generated": "grey",
};

function renderLessonAudioButton(frm) {
	frappe.call({
		method: "aimaticlearning.lms_learning.lesson_audio.get_status_for_lesson",
		args: { lesson: frm.doc.name },
		callback(r) {
			const status = (r.message && r.message.status) || "Not generated";
			frm.page.set_indicator(`Lesson Audio: ${status}`, STATUS_COLOR[status] || "grey");
			const label = status === "Ready" ? __("Regenerate Audio") : __("Generate Audio");
			frm.add_custom_button(label, () => confirmAndGenerate(frm), __("Lesson Audio"));
		},
	});
}

function confirmAndGenerate(frm) {
	frappe.call({
		method: "aimaticlearning.lms_learning.lesson_audio.get_generation_estimate",
		args: { lesson: frm.doc.name },
		callback(r) {
			const est = r.message;
			if (!est) return;
			if (!est.enabled) {
				frappe.msgprint(__("Lesson audio is disabled in Lesson Audio Settings."));
				return;
			}
			if (!est.course_enabled) {
				frappe.msgprint(
					__("This course is not in the enabled-courses list in Lesson Audio Settings.")
				);
				return;
			}
			frappe.confirm(
				__(
					"Generate audio for this lesson?<br>~{0} characters via {1}, estimated cost <b>${2}</b>.",
					[est.char_count, est.provider, est.estimated_usd]
				),
				() => triggerGeneration(frm)
			);
		},
	});
}

function triggerGeneration(frm) {
	frappe.call({
		method: "aimaticlearning.lms_learning.lesson_audio.generate_audio_for_lesson",
		args: { lesson: frm.doc.name },
		callback(r) {
			const result = r.message || {};
			frappe.show_alert({
				message: result.status === "queued" ? __("Audio generation queued") : result.message,
				indicator: result.status === "queued" ? "green" : "blue",
			});
			if (result.status === "queued") {
				setTimeout(() => renderLessonAudioButton(frm), 3000);
			}
		},
	});
}
