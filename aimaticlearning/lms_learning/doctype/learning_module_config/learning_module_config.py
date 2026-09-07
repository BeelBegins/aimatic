import frappe
from frappe.model.document import Document


class LearningModuleConfig(Document):
	def validate(self):
		self.refresh_counts()

	def refresh_counts(self):
		if not self.name:
			return
		self.chapter_count = frappe.db.count(
			"Learning Chapter Profile", {"learning_module": self.name}
		)
		self.published_flashcard_count = frappe.db.count(
			"Learning Flashcard",
			{"learning_module": self.name, "status": "Published"},
		)
		self.module_mcq_count = frappe.db.count(
			"Learning Question Meta",
			{"learning_module": self.name, "question_role": "Module Assessment"},
		)
