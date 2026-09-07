# FLK1 source-to-lesson coverage (notes)

**Date:** 2026-09-02  
**Site:** `lms.aimatic.tech`  
**Skill:** `lms-course-upload`  
**Backup / rollback:** `20260901_224450-lms_aimatic_tech-*`  
(`database.sql.gz` gzip -t ok; private-files.tar present)

Question-bank import has **not** started. This report is for review before MCQ work.

## Empty-lesson repair (done)

| Lesson | Course | Source File | body chars | content |
|---|---|---|---:|---|
| `0598 Draft notes — Chapter 4: Money Laundering` | `legal-services` | `56a723d3a4` | 35,018 | empty |
| `0621 Draft notes — Chapter 5: JUDICIAL REVIEW` | `public-law` | `e91c530519` | 15,534 | empty |
| `0649 Draft notes — Chapter 7: The Legal System of England and Wales` | `public-law` | `e91c530519` | 56,326 | empty |

HTML is in `Course Lesson.body` from the matching Word chapter (including following Heading 1 sections until the next Chapter N). Profiles store `source:<file> hash:<md5> locator:<heading>`. Topic lessons already under those chapters were left unchanged.

Guest `/lms/courses/legal-services/learn/4-1`, `/lms/courses/public-law/learn/5-1`, and `/learn/7-1` return HTTP 200 (LMS SPA). Authenticated learner rendering was not exercised in a browser this pass.

## Contract Law (`contract-law` / `ca1d016ce3`)

13 / 13 source chapters map to 13 live lessons. All have substantial body; `content` is empty. **Ready for question-bank dry-run after this report is reviewed** (`8921cae191`).

## Tort Law (`tort-law` / `8f112abe93`)

12 live lessons, all non-empty, `content` empty. Chapter numbers 1–8 have large source bodies aligned to the live lessons. Chapters 9–12 live lessons are large, but the Word parser currently attributes only short heading stubs to those numbers (most of the remaining Word text is still sitting in the Chapter 8 blob). **Heading-by-heading audit still required** before treating Tort notes as source-complete or importing `d7d445ef37`.

## Dispute Resolution (`dispute-resolution` / `6f5a6a3e41`)

20 live lessons, all non-empty, `content` empty. Source parse finds 21 chapters. **Gap:** Chapter 8 *Commencing Proceedings* (`CHAPTER8:COMMENCING PROCEEDINGS`, ~26k chars) has **no mapped LMS lesson**. Chapters 1–7 and 9–21 map. Do not start `44d3c2a638` until Chapter 8 has a lesson or an explicit omit decision.

## Also noted (not in the requested trio)

- Legal Services: Chapter 4 hub is now filled. Chapters 2 and 3 have no lesson titled `Chapter 2` / `Chapter 3` (topic-split structure). Money-laundering topic lessons remain as extra mapped rows.
- Public Law: Chapter 5 and 7 hubs are now filled. Chapter 6 *Human Rights* has no hub lesson; content lives in topic lessons (ECHR / ECtHR / HRA).

## Stop line

Do not import question banks until this report is reviewed, especially Tort 9–12 source split and DR Chapter 8.
