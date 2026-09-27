# 04 · Data and API contract

Source of truth: `moblab/contracts.py` (input contracts), `moblab/service.py` (commands),
`moblab/schema.sql` (tables and triggers), `moblab/web/routes_api.py` (routes). The HTML forms call
the same service commands as the JSON API, so every rule below applies to both.

## 1. Transport and controls

| Control | Behaviour |
|---|---|
| Binding | `127.0.0.1:5058` only (`python -m moblab run`). No other interface is offered. |
| Host header | Must be `127.0.0.1` or `localhost` (any port), otherwise **400 `HOST_NOT_ALLOWED`**. This is a DNS-rebinding defence. |
| CSRF | Every POST needs the session token, in header `X-CSRF-Token` (JSON) or form field `csrf_token`. Otherwise **403 `CSRF_FAILED`**. JSON clients get the token from `GET /api/session`. |
| Origin | If present, it must be the same localhost origin. `null` or a foreign origin → **403 `ORIGIN_NOT_ALLOWED`**. |
| Body size | 64 KB maximum → **413 `PAYLOAD_TOO_LARGE`**. |
| Media type | JSON API POSTs need `Content-Type: application/json` → otherwise **415**. |
| Read-only GETs | GET/HEAD handlers use a `PRAGMA query_only` connection, so they cannot write. |
| Headers | CSP `default-src 'self'` (no inline script or style), `nosniff`, `X-Frame-Options: DENY`, `no-store`. |
| Actor | `POST /api/session/actor {"actor_id": …}` stores a synthetic actor in the session. **This is not authentication.** |

## 2. Business-date authority

`meta.business_date` (the lab clock) is the **only** date the server uses to choose the effective
policy, stamp submissions, information requests and decisions, and gate migration. It moves forward
only (`POST /api/lab/clock`). No command accepts a caller-supplied business, submission, decision or
effective date: an unknown field → **422**. A `policy_version` sent with a decision is a **binding
assertion** that is checked against server state, and it never selects rules.

## 3. Command processing order

1. Validate the payload against its contract. Unknown fields, types, lengths and formats → **422 `VALIDATION_FAILED`** with `details.fields`.
2. `BEGIN IMMEDIATE`. From here on, any error rolls back **everything**, so no partial rows and no success event are left.
3. Actor (**403 `UNKNOWN_ACTOR`**) → role (**403 `FORBIDDEN_ROLE`**) → application (**404**) → self-review (**403 `SELF_REVIEW_FORBIDDEN`**).
4. Idempotency. Same `request_key` + same binding → replay the stored result (**200**, `"replayed": true`). Same key + any other binding → **409 `IDEMPOTENCY_KEY_CONFLICT`** with `details.differs_in`.
5. State and binding checks → writes → exactly one timeline event per changed case → store the idempotency record → `COMMIT`.

**Idempotency binding** = actor + operation + application reference + `revision_id` + `policy_version`
(or `target_version`) + `evaluation_id` + sha256 of the normalised payload. Only successful results are
stored, so a refused attempt does not reserve its key.

## 4. Input contracts (server-side)

Every string is trimmed. Control and invisible characters are rejected (newlines are allowed only in the
long-text fields). Integers must be JSON integers, and booleans are rejected. Every numeric id is limited to
`1 … 2^31−1` (DEF-001). `request_key` is required on every command: 8–64 characters from `[A-Za-z0-9_-]`.

| Field | Rule |
|---|---|
| `legal_name` | 2–120 chars. Letters, digits, space and `& ' . , ( ) - /`. Starts with a letter or digit. |
| `business_identifier` | `SYN-` + 6 digits. Unique. Cannot change after creation (no field in the draft contract). |
| `product_category` | `CARD_PRESENT`, `ECOMMERCE`, `SUBSCRIPTION`, `FUTURE_DELIVERY` |
| `volume_band` | `V1` (<50k/month), `V2` (50k–249,999), `V3` (250k–999,999), `V4` (1m+). These are fictional bands. |
| `activity_summary` | 0–400 chars, multiline, shown as escaped text |
| Document `doc_type` | `REG_EXTRACT`, `OWNERSHIP_DECL`, `BANK_CONFIRM`, `PROCESSING_HISTORY`, `SERVICE_SCOPE`, `OTHER` |
| Document `title` | 3–120 chars |
| Document `issued_on` | Real calendar date `YYYY-MM-DD`, between 2000-01-01 and 2099-12-31, and not after the business date (**422 `FUTURE_DATED_DOCUMENT`**) |
| Document `page_count` | 1–500 |
| Document `reference` | `SYN-DOC-` + 4–12 capitals/digits. Unique within the draft. |
| Documents per draft | at most 12 (**422 `DOCUMENT_LIMIT_REACHED`**) |
| `response_note` | Required on a resubmission (10–1000 chars). Must be empty on the first submission. |
| `message` (information request) | 20–1000 chars. `reason_code` is `MISSING_EVIDENCE`, `EVIDENCE_UNCLEAR`, `DATA_MISMATCH` or `OTHER`. `requested_doc_types` is at most 6 unique types. |
| `outcome` | `approve` or `reject` |
| `reason_code` (decision) | Approve: `EVIDENCE_VERIFIED`. Reject: `EVIDENCE_INCONSISTENT`, `OUTSIDE_RISK_APPETITE`, `UNRESPONSIVE_APPLICANT`, `OTHER` |
| `reason_text` | Approve: 10–1000 chars. Reject: 20–1000 chars. Reopen: 20–1000 chars. |
| `business_date` (lab clock) | Real ISO date, not earlier than the current lab date. The same date is a no-op (`changed: false`). |
| URL `MOB-####` | Anything else → **404** (never 500) |

## 5. Endpoints

| Method and path | Role | Body contract | Success |
|---|---|---|---|
| `GET /api/session` | any | — | 200 `{csrf_token, actor, business_date, effective_policy}` |
| `POST /api/session/actor` | any | `{actor_id}` | 200 |
| `GET /api/applications[?status=&product=&policy=]` | any | — | 200 list |
| `GET /api/applications/{ref}` | any | — | 200: application, revisions (documents, evaluations, decision, information requests), draft, events, migration items |
| `POST /api/applications` | specialist | `request_key, legal_name, business_identifier, product_category, volume_band, activity_summary?` | 201 `{reference, status:"draft"}` |
| `POST /api/applications/{ref}/draft` | specialist | `request_key` + any of `legal_name, product_category, volume_band, activity_summary` | 200 `{changed:[…]}` |
| `POST /api/applications/{ref}/documents` | specialist | `request_key, doc_type, title, issued_on, page_count, reference` | 201 `{document_id}` |
| `POST /api/applications/{ref}/documents/{id}/remove` | specialist | `request_key` | 200. Another case's or an unknown id → 404 |
| `POST /api/applications/{ref}/submit` | specialist | `request_key, response_note?` | 201 `{revision_id, revision_no, policy_version, evaluation_id, complete, unmet}` |
| `POST /api/applications/{ref}/request-information` | reviewer, not self | `request_key, revision_id, reason_code, message, requested_doc_types?` | 201 |
| `POST /api/applications/{ref}/decisions` | reviewer, not self | `request_key, outcome, revision_id, policy_version, evaluation_id, reason_code, reason_text` | 201 `{decision_id, status, revision_no, policy_version, evaluation_id, snapshot_sha256}` |
| `POST /api/applications/{ref}/reopen` | reviewer, not self | `request_key, reason_text` | 200 `{status:"draft", cycle, preserved_decision_id}` |
| `GET /api/policy/impact` | any | — | 200: read-only preview and recorded runs |
| `POST /api/policy/migrations` | policy owner | `request_key, target_version` | 201 `{run_id, counts, items}` |
| `POST /api/lab/clock` | any role except auditor | `{business_date}` | 200 |
| `PATCH/PUT/DELETE /api/applications/{ref}`, any method on `…/status` | — | — | **405 `METHOD_NOT_ALLOWED`**. There is no generic update. |

Error body: `{"error": {"code": "…", "message": "…", "details": {…}}}`.

## 6. Decision binding checks (in order)

| Check | Failure |
|---|---|
| Status is `submitted` | Terminal → 409 `ALREADY_DECIDED`. Otherwise 409 `INVALID_TRANSITION`. |
| `revision_id` belongs to this application | 422 `REVISION_APPLICATION_MISMATCH` |
| `revision_id` is the current revision | 409 `STALE_REVISION` |
| The latest evaluation's policy is the one effective on the business date | 409 `POLICY_MIGRATION_REQUIRED` |
| The cited `policy_version` and `evaluation_id` equal that evaluation | 409 `STALE_POLICY_EVALUATION` |
| Recomputed content sha256 = stored revision hash = hash the evaluation saw | 409 `REVISION_INTEGRITY_FAILURE` |
| Approval only: the evaluation is complete | 422 `EVIDENCE_INCOMPLETE` (`details.unmet`) |

## 7. Roles

| Command | Specialist | Reviewer | Policy owner | Auditor |
|---|---|---|---|---|
| Create, edit draft, add/remove documents, submit/resubmit | ✓ | — | — | — |
| Request information, approve, reject, reopen (rejected only) | — | ✓ unless they **created the application or submitted any of its revisions** (DEC-12) | — | — |
| Preview impact | ✓ | ✓ | ✓ | ✓ |
| Apply policy version (migration) | — | — | ✓ | — |
| Move lab clock (simulation control) | ✓ | ✓ | ✓ | — |
| Read pages, API and exports | ✓ | ✓ | ✓ | ✓ |

Kai holds both specialist and reviewer roles, so on MOB-0008 only the self-review rule stops them.

## 8. Data model

| Table | Mutability | Purpose |
|---|---|---|
| `meta` | lab clock only | schema version, `business_date` |
| `actor` | seed only | synthetic actors and roles |
| `policy_version` | **immutable** (triggers) | v1/v2 rules JSON + sha256, loaded from `policies/*.toml` |
| `application` | status via commands only | identity (immutable trigger), status (transition trigger), current revision, cycle, `row_version` (compare-and-set) and working-draft fields (editable only in draft/needs_information) |
| `draft_document` | insert/delete in draft/needs_information only, no update | working evidence. `AUTOINCREMENT`, so ids are never reused (DEF-002). |
| `application_revision`, `revision_document` | **immutable** | frozen submitted content + `content_sha256` |
| `policy_evaluation` | **immutable**. Unique per (revision, policy) | engine result, `reference_date` = submission date, trigger `submission`/`migration` |
| `info_request` | **immutable** | reviewer or migration requests with reason |
| `decision` | **immutable**. Unique per revision. At most one approval per application | outcome, reason, bindings, frozen snapshot + sha256 |
| `event` | **append-only** | timeline and audit |
| `migration_run`, `migration_item` | **immutable** | each policy application and its per-case classification |
| `idempotency_record` | **immutable** | request-key bindings and stored responses |

Database-level guards: status changes outside the allowed transitions abort. An `approved`/`rejected` status
requires a matching decision row. Identity columns cannot change. Draft evidence is locked outside
draft/needs_information. UPDATE/DELETE on history tables aborts.

## 9. Exports

- `GET /export/cases.csv`: cells starting with `= + - @`, tab or CR are prefixed with `'` (formula-injection defence).
- `GET /cases/{ref}/export.json`: `application/json` download with a synthetic-data notice.
