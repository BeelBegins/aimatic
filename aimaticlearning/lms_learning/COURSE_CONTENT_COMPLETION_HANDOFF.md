# Examic Study FLK1 course-content completion handoff

## Objective

Finish the uploaded SQE1 FLK1 content as a coherent student product at
`lms.aimatic.tech`. Students must be able to choose a subject, read complete
and well-formatted notes, practise reviewed MCQs, revise with flashcards, and
take a subject mock when one is ready. Do not merge all subjects into the BLP
course: retain one LMS Course per subject, grouped as **SQE1 · FLK1**.

The user expects a complete learning experience, not private documents or
empty course shells. Use legal knowledge to draft *missing explanatory content*
only where the uploaded source leaves a genuine gap; label it as a review draft
and obtain UK legal-content approval before it becomes student-facing.

## Scope and safety

- Authoritative site: `lms.aimatic.tech` in `/home/nabeel/frappe-bench`.
- Owned implementation only: `apps/aimatic/aimatic/lms_learning/`. Never edit
  `apps/lms`, Frappe, ERPNext, OFBiz, Caddy, retail/POS, `szl`, `siezal`,
  `matic`, or `hsm`.
- Preserve the private Word sources. Do not attach them as learner downloads.
- Before every LMS data import/publish operation: verify site role, create an
  LMS backup with files, verify it, state rollback, then mutate narrowly.
- Never assume an answer key because an option appears first or is labelled A.
  An MCQ cannot be published until its correct answer, explanation, difficulty,
  concept and source reference are explicit and reviewed.
- Do not auto-enrol students in all subjects. They choose a course or an
  administrator/invitation enrols them.

## Verified private source inventory

Archive File `710d68f837` (`/private/files/Examicstudy.rar`) was expanded and
its source documents registered as private Frappe Files in `Home/examic-study`:

| Subject | Notes source | Question-bank source |
|---|---|---|
| Business Law & Practice | existing `BLP notes.docx` / module `LMOD-00017` | bundled in BLP source |
| Contract Law | `ca1d016ce3` — Study Guide | `8921cae191` — Practice Questions |
| Dispute Resolution | `6f5a6a3e41` — Notes | `44d3c2a638` — Question Bank |
| Tort Law | `8f112abe93` — Study Guide | `d7d445ef37` — Questions |
| Legal Services | `56a723d3a4` — Notes | none supplied |
| Legal System, Public Law & EU Law | `e91c530519` — Public Law | none supplied |

The two Contract source records have harmless hash-suffixed duplicate File rows;
use the IDs above as the canonical records. All sources are private.

## Current live course state — verified 2026-09-02

| Course | LMS course ID | Notes lessons with content | Current gap | Activities ready |
|---|---|---:|---|---|
| Business Law & Practice | `business-law-practice-blp` | 80 / 80 | Source-outline audit still required | Existing BLP activities require QA before new claims |
| Contract Law | `contract-law` | 13 / 13 | None found | Notes only |
| Dispute Resolution | `dispute-resolution` | 20 / 20 | None found | Notes only |
| Tort Law | `tort-law` | 12 / 12 | None found | Notes only |
| Legal Services | `legal-services` | 7 / 8 | **Chapter 4: Money Laundering is empty** | No question bank supplied |
| Legal System, Public Law & EU Law | `public-law` | 23 / 25 | **Chapter 5: Judicial Review** and **Chapter 7: The Legal System of England and Wales are empty** | No question bank supplied |

All six courses are published and self-learning is enabled. They are grouped
under `SQE1 · FLK1`. LMS lesson rails now include an **SQE1 · FLK1 — Switch
subject** menu so students can move between the six separate courses.

## Required delivery order

### 1. Repair the three empty published note lessons

Source these sections from their registered Word documents, render clean
student-facing HTML in `Course Lesson.body`, and keep `Course Lesson.content`
empty. LMS prefers `content` over `body`; leaving old EditorJS JSON in
`content` causes raw/duplicate text to reappear.

1. `0598 Draft notes — Chapter 4: Money Laundering` — Legal Services.
2. `0621 Draft notes — Chapter 5: JUDICIAL REVIEW` — Public Law.
3. `0649 Draft notes — Chapter 7: The Legal System of England and Wales` —
   Public Law.

For every imported lesson:

- heading hierarchy must reflect the source;
- body text must be readable on desktop and mobile (paragraphs, tables/lists
  converted cleanly, no raw document controls);
- save a private source reference, source hash and source-section locator in
  the matching Learning Chapter Profile / import metadata;
- set `content = ""`, preserve lesson names and progress records;
- test each resulting `/learn/<chapter>-1` route as a learner.

### 2. Complete the note imports from the supplied sources

Audit each course against its Word source heading-by-heading. Do not assume a
chapter is complete merely because it has a non-empty lesson. Each chapter
needs a title, focused introduction, all substantive source sections, clean
tables/lists, and an explicit learning objective.

Where the source is structurally incomplete, create a `Review draft —
supplement` section using reliable UK-law knowledge and a cited authoritative
reference. It stays unpublished until a UK-qualified reviewer approves it.
Do not fabricate statute, case, procedure or SQE assessment claims.

### 3. Build activities subject by subject

Priority: **Contract Law → Tort Law → Dispute Resolution → Legal Services →
Public Law**.

For Contract, Tort and Dispute Resolution:

1. Parse the supplied question bank into a dry-run report.
2. Produce a reviewer spreadsheet/JSON containing stem, all options, proposed
   answer, explanation, difficulty, chapter, concept, source locator, hash,
   and duplicate/malformed flags.
3. Have a UK legal reviewer approve the answer key and explanations.
4. Import versioned chapter MCQs. Preserve attempts against their revision.
5. Create reviewed flashcards from the approved notes/questions; tag every card
   with chapter, concept, difficulty and source reference.
6. Build a documented subject mock blueprint only after enough reviewed items
   exist. Never pad a mock by duplicating questions.

For Legal Services and Public Law:

- finish notes first;
- create an assessment blueprint and a coverage-gap report;
- use expert-authored, reviewed questions for gaps because no question-bank
  document was supplied; do not manufacture scored questions as if sourced.

### 4. Make the FLK1 pathway coherent

- Keep BLP, Contract, Dispute Resolution, Tort, Public Law and Legal Services
  as separate courses for progress and enrolment.
- Present them in FLK1 assessment order using the existing category and lesson
  subject switcher.
- On the course catalogue / FLK1 surface, show accurate labels: `Study notes`
  until practice and revision activities have passed review. Do not advertise
  MCQs, flashcards or mocks for a subject before they are available.
- Do not show personal learner statistics to new students. Preserve the
  existing statistics access restriction.

## Implementation starting points

- Source registration and chapter parsing:
  `aimaticlearning.lms_learning.draft_subject_import`.
- Existing BLP source import/presentation:
  `import_pipeline.py`, `kinnu_course.py`, `course_presentation.py`.
- Question parsing/import baseline: `mcq_import.py`.
- Flashcards and protected delivery: `api.py`, `content_generation.py`.
- FLK1 grouping: `sqe_pathway.py`.
- Student UI assets: `public/js/lms_student_experience.js` and
  `public/css/lms_student_experience.css`.

Never run a broad re-import against BLP without a dry run: it has learner
progress and existing activity records.

## Definition of done per subject

- [ ] Every supplied note heading is mapped to a non-empty learner lesson.
- [ ] Empty/duplicate/stale `content` payloads are absent.
- [ ] Notes are formatted and justified/readable; no document artefacts,
      internal labels, author names or raw tool links appear.
- [ ] Every published MCQ has a reviewed answer, explanation, difficulty,
      concept, chapter, source locator and revision.
- [ ] Flashcards are reviewed, tagged and usable through the student session
      lifecycle (start, rate hard/good/easy, end/resume).
- [ ] A learner can discover the subject, enrol deliberately, open notes,
      complete a session, leave and resume safely on desktop and mobile.
- [ ] Course catalogue, FLK1 switcher and course labels match real readiness.
- [ ] Fresh backup, import report, QA evidence and rollback identifier recorded.

## Immediate first task

Repair the three named empty lessons, then produce a source-to-lesson coverage
report for Contract, Tort and Dispute Resolution. Only after that report is
reviewed should the question-bank import begin.
