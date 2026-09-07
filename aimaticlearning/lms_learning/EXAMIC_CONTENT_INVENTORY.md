# Examic Study source inventory and FLK1 import plan

Audited on 2026-09-01 against `lms.aimatic.tech` File `710d68f837`.

## Source archive

- Frappe File: `710d68f837`
- Private URL: `/private/files/Examicstudy.rar`
- Frappe folder: `Home/examic-study`
- Size: 2,293,001 bytes
- SHA-256: `dd5594b5f095eae89dae730c07e24f7eb3b23bfa580b73844238da416846edab`
- Contents: 7 directories, 9 Word documents, and 1 original brand image

The eight non-BLP Word files currently exist **inside the private RAR only**. They are not separate Frappe File records and are not imported into live LMS courses.

## Verified inventory

| Subject | Source file | Size | Structural indicator | Verified LMS status |
|---|---|---:|---:|---|
| Business Law and Practice | `BLP notes and Questions .docx` | 375,919 B | 121,094 words; 21 headings; 180 option-A question blocks | Source imported. SHA-256 exactly matches the retained original `BLP notes.docx`. Live presentation currently maps sections 1, 3–9 and 11 into four chapter hubs; completeness against all 21 source headings still needs academic review. |
| Contract Law | `SQE1 Contract Law Study Guide.docx` | 133,895 B | 14,304 words; 4 tables | Not imported. |
| Contract Law | `SQE1 Contract Law Practice Questions.docx` | 222,950 B | 43,650 words; about 260 option-A question blocks | Not imported. |
| Dispute Resolution | `DR - Notes - FLK1 - SQE.docx` | 321,185 B | 73,020 words; 3 tables | Not imported. |
| Dispute Resolution | `Dispute Resolution - Quesiton Bank.docx` | 317,693 B | 79,397 words; about 420 option-A question blocks | Not imported. Filename typo is preserved from the source archive. |
| Legal Services | `LEGAL SERVICES.docx` | 69,467 B | 19,712 words; 26 headings | Not imported; no separate question bank supplied. |
| Public Law | `Public Law.docx` | 92,180 B | 19,633 words; 220 headings/subheadings | Not imported; no separate question bank supplied. |
| Tort Law | `Tort Law - STUDY GUIDE.docx` | 634,939 B | 69,975 words; 3 tables | Not imported. |
| Tort Law | `Tort Law Questions .docx` | 153,283 B | 27,320 words; about 240 option-A question blocks | Not imported. |
| Brand | `examic study logo and colors and design.jpeg` | 29,589 B | 1280 × 565 JPEG | Exact source verified and used to derive production header/favicon crops; original retained unchanged. |

Question counts above are structural indicators, not approved import totals. The parser and a human reviewer must validate every stem, option, answer, explanation, duplicate, and source reference before publication.

## Current live BLP state

- 1 published LMS Course: Business Law and Practice
- 4 active chapter hubs
- 12 topic lessons
- 180 chapter-practice MCQs
- 200 published flashcards
- 150-question module mock
- 2 current BLP enrollments

The live BLP source is verified; the remaining archive content must not be described as uploaded to courses.

## Kinnu-informed FLK1 product model

Use the reference model's strengths without copying its branding or unverified claims:

1. One persistent FLK1 pathway that groups subject modules.
2. Subject/chapter navigation on the left, the selected lesson in the centre, and lesson-aware Study Buddy context on the right.
3. Notes, practice MCQs, flashcards and a mock as first-class actions—not document attachments.
4. One clear next action at a time, with progress and resume state visible.
5. Separate LMS Course records per subject for permissions, progress and release control; the pathway shell groups them under FLK1.

## Student-facing UX benchmark

**Role model:** [Kinnu SQE](https://www.kinnu.xyz/law/sqe), reviewed on
2026-09-07. Use Kinnu as a student-facing UX benchmark for information
architecture and learning flow—not as a source of legal content, answer keys,
branding, visual assets, pricing, marketing claims, or backend structure.

Adopt these benchmark journeys for Examic Study:

1. **Start:** one `SQE1` entry point with clear `FLK1` and `FLK2` switches; do not expose every chapter as a course card.
2. **Choose:** show subject modules, then chapters inside the selected subject with progress and resume state.
3. **Study:** make `Study Notes`, `Practice MCQs`, `Flashcards`, and `Mock Exams` first-class actions in one consistent shell.
4. **Review:** show answer explanations, weak areas, timing, topic progress, and the next recommended action without hiding the source reference.
5. **Everywhere:** preserve the same hierarchy and primary actions on desktop and mobile, with accessible labels and no learner dependence on technical LMS names.

Kinnu's current SQE product presents MCQs, study notes, mock exams, flashcards,
progress analysis, and an SQE-specific assistant as one exam-preparation
experience. Examic Study must apply the same clarity while keeping legal
answers grounded only in approved UK SQE sources and independently reviewed.

## Governed import sequence

1. **Register sources privately.** Extract each Word document into its own private Frappe File, record SHA-256, subject, revision, and approval owner. Do not link Word downloads from lessons.
2. **Parse into drafts only.** Build a source-specific outline and question parser. Produce a dry-run report containing chapter titles, lesson blocks, candidate MCQs, malformed questions, answer/explanation coverage and duplicates.
3. **Academic sign-off.** A legal content reviewer approves the outline and question answer key. AI may assist with formatting or draft flashcards only from approved material.
4. **Create unpublished subject courses.** Recommended readiness order: Contract Law, Tort Law, Dispute Resolution, then Public Law and Legal Services after question-bank gaps are resolved.
5. **Import traceably.** Tag each question and flashcard with subject, chapter, concept, difficulty, source reference, source SHA and revision. Preserve question revisions against attempts.
6. **QA before publication.** Test enrolment permissions, protected notes, lesson order, question scoring, answer feedback, flashcard review states, resume/abandon behaviour, mobile layout and accessibility.
7. **Publish deliberately.** Publish one reviewed subject at a time and add it to the FLK1 pathway. Never auto-enrol every learner or silently replace existing BLP progress.

## Per-subject release gates

- Approved chapter/lesson outline
- 100% question stems and answer keys parsed
- Explanations present or explicitly queued for review
- Duplicate and malformed-question report resolved
- Difficulty and source-reference tags complete
- Flashcards human-reviewed before `Published`
- Mock blueprint documented with no silent duplication
- Test learner passes notes, MCQ, flashcard, resume and results checks
- Fresh backup and rollback identifiers recorded before live import
