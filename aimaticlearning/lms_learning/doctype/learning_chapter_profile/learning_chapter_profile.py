import frappe
from frappe.model.document import Document


class LearningChapterProfile(Document):
	def validate(self):
		self.refresh_mcq_count()

	def refresh_mcq_count(self):
		if not self.name:
			return
		self.mcq_count = frappe.db.count(
			"Learning Question Meta",
			{
				"learning_module": self.learning_module,
				"course_chapter": self.course_chapter,
				"question_role": "Chapter MCQ",
			},
		)
