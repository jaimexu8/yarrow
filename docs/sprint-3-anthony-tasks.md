# Sprint 3 — Implementation guide for Anthony

**Project:** Yarrow, Purdue CS 40700, Team 13  
**Planning source:** [Sprint 3 Planning (Google Docs)](https://docs.google.com/document/d/1EZ-dU_UTvxyAFux0_hC9a3sexEcPazPGVgW0l3ixupQ/edit)  
**Workload:** 40 estimated hours across four stories. The Google Doc is the shared planning source; align changes with the team before implementation.

## Development order

1. **US-116 (6h):** First-time user onboarding. Fast, visible feature; exercise sign-up, upload and dashboard.
2. **US-114 (12h):** Upload history and batch details. Build authenticated batch-list/detail APIs and a frontend history view.
3. **US-115 (14h):** Bulk export. Reuse existing single-document export logic, build ZIP packaging and a library selection action.
4. **NFR-23 (8h):** Production configuration safeguards. Validate environment settings, separate demo seed accounts from production and add a smoke check.

For each story: create a feature branch from current main, implement backend and frontend work, add tests for the acceptance criteria, run the relevant checks, open a pull request and confirm the demo behavior before considering the story complete. Avoid committing credentials or real personal documents.

## 114: View previous upload batches

**Owner: Anthony McCrovitz | Total Hours: 12 hrs**

Add an Upload History view that lists a user's past batches and lets them open a batch to see its documents and processing results.

| Task | Estimate |
| --- | ---: |
| Add authenticated APIs to list a user's upload batches and the documents in each batch | 4 hrs |
| Build an Upload History page showing batch dates, file counts, and processing states | 3 hrs |
| Add a batch detail view with per-document status, links, and refresh while processing | 3 hrs |
| Unit testing and integration testing | 2 hrs |

**Acceptance criteria**
- Given a user has uploaded several batches, when they open Upload History, they should see those batches with upload dates and processing summaries.
- Given a user opens a batch, when its detail view loads, they should see the files in that batch, their processing status, and links to the documents.
- Given a batch is still processing, when a document changes status, the batch details should update without needing another upload.
- Given a user with no previous uploads, when they open Upload History, they should see a helpful empty state.
- Given a batch belongs to another user, when an unauthorized user requests it, the API should deny access.

## 115: Export multiple processed documents at once

**Owner: Anthony McCrovitz | Total Hours: 14 hrs**

Extend the existing single-document export workflow to download a selection of completed documents together in one ZIP archive, using the formats Yarrow already supports.

| Task | Estimate |
| --- | ---: |
| Add a bulk-export API that validates document ownership and completed status and reuses existing export logic | 4 hrs |
| Build ZIP packaging with clear filenames, collision handling, and limits on archive size | 3 hrs |
| Add an Export Selected action on the document library with a format selector and progress/error feedback | 4 hrs |
| Unit testing and integration testing | 3 hrs |

**Acceptance criteria**
- Given a user selects multiple completed documents, when they choose an export format, they should receive a ZIP containing the extracted content for each document.
- Given two selected documents have the same name, when the ZIP is created, both exported files should have distinct names.
- Given a selected document belongs to another user, when bulk export is requested, the API should reject the request without exposing its contents.
- Given a selected document is not finished processing, when the user requests export, the interface should explain why it cannot be exported.
- Given a user exports a single document, when they use the existing Export menu, the original single-document export should continue to work.

## 116: Guidance and sample document for first-time users

**Owner: Anthony McCrovitz | Total Hours: 6 hrs**

Improve the empty dashboard and upload flow for new accounts by explaining the main steps and offering a safe example PDF to upload and process.

| Task | Estimate |
| --- | ---: |
| Add a dashboard empty state with brief guidance and a clear action to start uploading | 2 hrs |
| Add a synthetic example PDF and a Try Sample Document action using the existing upload flow | 2 hrs |
| Unit testing, accessibility checks, and integration testing | 2 hrs |

**Acceptance criteria**
- Given a new account with no documents, when the user opens the dashboard, they should see an explanation of how to get started.
- Given a user selects Try Sample Document, when the upload finishes, the example should appear in their own document library and follow the normal processing workflow.
- Given the sample document is inspected, when it is opened, it should contain only fictional content and no personal information.
- Given a user has uploaded a document, when they return to the dashboard, the first-time empty state should no longer appear.
- Given a user navigates by keyboard, when they use the onboarding actions, the controls should remain usable and clearly labeled.

## NFR-23: Production configuration checks

**Owner: Anthony McCrovitz | Total Hours: 8 hrs**

As an operator, I would like production configuration checks so that development settings and demo credentials cannot accidentally be used in a release. Add release safeguards around required environment settings and example accounts, then verify that a deployment starts correctly before it is presented or released.

| Task | Estimate |
| --- | ---: |
| Validate required production settings and fail startup when critical credentials or configuration values are missing | 2 hrs |
| Restrict sample users and default passwords to development-only setup and document safe production seeding | 2 hrs |
| Add a repeatable deployment smoke check for startup, health, and authenticated API availability | 2 hrs |
| Unit testing, configuration regression tests, and release documentation | 2 hrs |

**Acceptance criteria**
- Given a production deployment is missing required secrets, when the service starts, it should fail with a clear configuration error instead of using unsafe defaults.
- Given production mode is enabled, when the database is initialized, demo users and documented default passwords should not be created or accepted.
- Given a correctly configured staging deployment, when the smoke check runs, it should verify that the service starts and responds to its health and authenticated API checks.
- Given development mode is enabled, when local setup is performed, developers should still be able to create explicitly marked sample accounts for testing.
- Given a deployment check fails, when the release is reviewed, the failure should be visible and block the release until it is corrected.

## Existing application touchpoints

- Upload UI: `frontend/src/app/(dashboard)/upload/page.tsx`
- Document library/dashboard UI: `frontend/src/components/documents/DocumentTable.tsx` and `frontend/src/app/(dashboard)/dashboard/page.tsx`
- Client API functions: `frontend/src/lib/documents.ts` and `frontend/src/lib/export.ts`
- Backend document APIs: `backend/app/api/v1/endpoints/documents.py`
- Development environment: `docker-compose.yml`, `.env.example`, and existing deployment/seed scripts (inspect before changing)

These are entry points, not an exhaustive implementation map. Reuse existing ownership checks, batch status logic and export formats. Coordinate with teammates to avoid editing the same API/routes or schema migrations simultaneously.

## What counts as done

- Changes are merged to `main` with passing checks and review.
- Acceptance criteria have tests and a short demonstrable user workflow.
- Existing uploads, single-document exports and account login still work.
- Production settings reject unsafe defaults; any local demo credentials remain development-only.

**Planning status:** This is a developer-facing reference. The full team planning PDF is separately available in this directory; GitHub documentation does not replace a required Brightspace submission.
