# BLP LMS module — implementation & developer runbook

Site: **https://lms.aimatic.tech**  
Source: private file `0732ba20ab` → `/private/files/BLP notes.docx` (21 chapters detected)

## Product layers (what we built)

| Layer | Requirement | Implementation |
|-------|-------------|----------------|
| 1 | Read-only notes, no download | `Learning Chapter Profile.notes_html` + `/learning-notes/<profile>` viewer with enrolment gate |
| 1b | 20 MCQs per chapter + feedback | Native `LMS Quiz` per chapter + `LMS Question` explanations + `Learning Question Meta` tags |
| 2 | Strengths/weaknesses + visual map | `Learning Attempt Detail` + `analytics.build_learning_map()` + Desk page **Learning Map** |
| 3 | 200 AI flashcards (review before publish) | `Learning Flashcard` (`Draft` → `Under Review` → `Published`) |
| 4 | 150 MCQ module assessment | `build_module_assessment_blueprint()` selects from chapter pool into module quiz |

All Aimatic code lives in `apps/aimatic/aimatic/lms_learning/` (upstream `lms` is not modified).

## Developer: run everything (ordered)

### 1. Install extension on LMS site

```bash
cd /home/nabeel/frappe-bench
bench --site lms.aimatic.tech install-app aimatic
bench --site lms.aimatic.tech migrate
bench build --app aimatic
bench --site lms.aimatic.tech clear-cache
```

If web workers were started with `--preload`, restart web after install:

```bash
sudo supervisorctl restart frappe-bench-web:frappe-bench-frappe-web
```

### 2. Import course structure from Word (21 chapters)

```bash
bench --site lms.aimatic.tech execute aimaticlearning.lms_learning.import_pipeline.import_blp_module_from_file
```

Creates:

- `LMS Course`: **Business Law & Practice (BLP)**
- 21 `Course Chapter` + protected-notes lessons + chapter quiz shells (20 marks each)
- `Learning Module Config` record with targets: **20 / 150 / 200**

Verify in Desk → **LMS Learning** workspace.

### 3. Export chapter text for AI drafting

```bash
bench --site lms.aimatic.tech execute aimaticlearning.lms_learning.content_generation.export_chapter_source_bundle --kwargs '{"learning_module": "LMOD-00017"}'
```

Replace `LMOD-00017` with your module name. JSON lands in `sites/lms.aimatic.tech/private/files/lms_learning_exports/`.

Use bundled `ai_prompts` to generate:

- **420 chapter MCQs** (20 × 21 chapters) — human review required
- **200 flashcards** — human review required

**Rules:** draft only from approved notes; tag `concept`, `difficulty`, `source_reference`; set `ai_generated: true`.
AI-generated flashcards must also include `source_quote`, copied verbatim from the chapter `notes_html`; the import rejects any quote not found in those notes.

### 4. Import reviewed MCQs (per chapter batch)

API (Desk console or script):

```python
import json, frappe
payload = json.load(open("chapter-01-mcqs.json"))
frappe.call("aimaticlearning.lms_learning.api.import_generated_mcq_json", {
    "learning_module": "LMOD-00017",
    "payload": json.dumps(payload),
})
```

JSON shape (array or `{ "questions": [...] }`):

```json
{
  "chapter_profile": "abc123hash",
  "question": "Who pays income tax?",
  "options": [
    {"text": "Only companies", "is_correct": false, "explanation": "…"},
    {"text": "Individuals and others per statute", "is_correct": true, "explanation": "…"}
  ],
  "concept": "Income tax scope",
  "difficulty": "Medium",
  "source_reference": "BLP notes ch.1 para 3",
  "source_quote": "The approved notes sentence supporting this card.",
  "ai_generated": true
}
```

Repeat until each chapter profile shows **mcq_count = 20**.

### 5. Import reviewed flashcards

```python
frappe.call("aimaticlearning.lms_learning.api.import_generated_flashcard_json", {
    "learning_module": "LMOD-00017",
    "payload": json.dumps({"flashcards": [...]}),
})
```

Publish after review: set `status` to **Published** (bulk update or reviewer workflow).

Target: **200 published** flashcards (`Learning Module Config.published_flashcard_count`).

### 6. Build 150-question module assessment

```bash
bench --site lms.aimatic.tech execute aimaticlearning.lms_learning.content_generation.build_module_assessment_blueprint --kwargs '{"learning_module": "LMOD-00017"}'
```

Selects evenly across chapters from the chapter MCQ pool (no silent duplicate questions). Updates `LMS Quiz` **BLP Module Assessment (150 MCQs)**.

### 7. Learner flow (manual QA)

1. Enrol a test user on the BLP course.
2. Open chapter lesson → **Open protected notes** link → confirm HTML renders, no file download.
3. Complete chapter quiz → confirm per-option explanations (LMS `show_answers`).
4. Desk → **Learning Map** → strengths/weaknesses populate after attempts.
5. Flashcard deck API: `aimaticlearning.lms_learning.api.get_flashcard_deck`.

### 8. Analytics hooks for LMS quiz UI

When wiring the LMS frontend quiz timer, call after each question:

```javascript
frappe.call("aimaticlearning.lms_learning.api.record_attempt_detail", {
  learning_module: "LMOD-00017",
  lms_question: question_id,
  correct: is_correct,
  time_seconds: elapsed,
  course_chapter: chapter_id,
  quiz_submission: submission_id,
});
```

## DocTypes

- `Learning Module Config` — module blueprint + targets
- `Learning Chapter Profile` — protected HTML + links to chapter/quiz/lesson
- `Learning Question Meta` — tags on `LMS Question`
- `Learning Flashcard` — flashcard bank with review status
- `Learning Attempt Detail` — granular attempt telemetry

## APIs (whitelisted)

- `aimaticlearning.lms_learning.api.get_protected_chapter_notes`
- `aimaticlearning.lms_learning.api.get_learning_map`
- `aimaticlearning.lms_learning.api.record_attempt_detail`
- `aimaticlearning.lms_learning.api.get_flashcard_deck`
- `aimaticlearning.lms_learning.api.review_flashcard`
- `aimaticlearning.lms_learning.api.run_blp_import`
- `aimaticlearning.lms_learning.api.export_content_bundle`
- `aimaticlearning.lms_learning.api.import_generated_mcq_json`
- `aimaticlearning.lms_learning.api.import_generated_flashcard_json`
- `aimaticlearning.lms_learning.api.import_review_mcqs`
- `aimaticlearning.lms_learning.api.build_module_assessment`

## Content targets checklist

- [ ] 21 chapters imported from docx
- [ ] 420 chapter MCQs imported & linked (20 each)
- [ ] 200 flashcards imported & published
- [ ] 150 module assessment MCQs blueprint built
- [ ] Learning map verified with real attempt data
- [ ] Source Word file remains private (never attached to public lessons)

## Student accounts, enrollment & email

Frappe LMS handles the learner lifecycle natively; Aimatic adds enrollment emails and invite helpers.

### Student account paths

| Path | How | Role |
|------|-----|------|
| **Self-signup** | `/login` → Sign up (LMS signup form) | `LMS Student` (LMS `User` hook + Portal default role) |
| **Admin invite** | API below or Desk → User | `LMS Student` + optional auto-enrol |
| **Self-enrol** | Course page → **Enroll** (free course) | Creates `LMS Enrollment` |

**Configured on site:** `disable_signup = 0`, default role `LMS Student`, BLP `disable_self_learning = 0`.

### Course enrollment

- Free course: learner clicks **Enroll** → `LMS Enrollment` row (native LMS UI).
- Instructor enrol: LMS → **Learning** → **LMS Enrollment** or Course Enrollment form.
- Aimatic bulk invite:

```python
frappe.call("aimaticlearning.lms_learning.api.bulk_invite", {
    "emails": "student1@example.com|Alice\nstudent2@example.com|Bob",
    "course": "business-law-practice-blp",
})
```

Single invite: `aimaticlearning.lms_learning.api.invite_student`

### Email integrations

| Event | Template | Trigger |
|-------|----------|---------|
| New website learner (no enrollment yet) | `Aimatic LMS Student Welcome` | `User` after_insert |
| Course enrollment | `Aimatic LMS Course Enrollment` | `LMS Enrollment` after_insert |

Templates are created by `configure_lms_student_access` patch. Emails are **queued** via `frappe.sendmail`.

**Required for delivery:** Desk → **Email Account** → outgoing SMTP (or SendGrid/etc.) marked default. Without this, enrollments work but emails stay queued/fail silently in logs.

Also set **LMS Settings → Contact Us Email** for support line in templates.

Re-apply LMS student config:

```bash
bench --site lms.aimatic.tech execute aimaticlearning.lms_learning.enrollment.configure_lms_student_access
```

Or: `aimaticlearning.lms_learning.api.configure_student_access`

### Learning map & analytics

Enrollment is required before protected notes, quizzes, flashcards, and the learning map record attempts.


Notes are server-rendered HTML after enrolment checks. This blocks casual file download but cannot prevent screenshots or copy/paste—do not claim otherwise in learner messaging.
