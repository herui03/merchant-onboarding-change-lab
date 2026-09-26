# Defect log (real defects found during development)

Only defects that were actually observed are listed. Each product defect was reproduced
**before** it was fixed, and each has a regression test that fails on the old code. Test-harness
mistakes are listed separately, because they were bugs in the tests, not in the product.

## Product defects

| ID | Found by | How it was found | Symptom (reproduced) | Root cause | Fix | Regression test | Status |
|---|---|---|---|---|---|---|---|
| DEF-001 | Codex source review R3-01 | Reported by Codex, then reproduced by Claude on a temporary seeded database through the real service | `remove_document("jordan", "MOB-0006", 2**100, …)` raised a raw `OverflowError: Python int too large to convert to SQLite INTEGER` instead of the documented 404. The transaction rolled back, so the fingerprint was unchanged, but the HTTP layer would have returned 500. | The document-id guard rejected bools, non-ints and values < 1, but had no upper bound before binding to SQLite. | One documented id range, `1..2^31-1` (`contracts.ID_MAX`, `is_valid_id`), used for document, revision and evaluation ids. | `test_out_of_range_document_id_is_a_404_not_a_crash` (8 cases), `test_out_of_range_revision_and_evaluation_ids_are_422` (4 cases). The HTTP-path case is added with the routes. Verified failing on the old guard. | Fixed |
| DEF-002 | Codex source review R3-02 | Reported by Codex, then reproduced by Claude with its exact steps through the real service | Add document A (id 26) → remove A → add replacement B → B also got id 26 → a stale "remove 26" **deleted B**. The fingerprint changed. | `draft_document.id` was a plain `INTEGER PRIMARY KEY`. SQLite reuses the highest deleted rowid, and draft documents are the only deletable rows. | `INTEGER PRIMARY KEY AUTOINCREMENT` on `draft_document`, so ids are never reissued. The stale removal now returns 404 and changes nothing. | `test_stale_document_removal_cannot_delete_a_replacement`. Verified failing with the old schema. | Fixed |
| DEF-003 | Claude smoke test (before the first UI commit) | Requested every GET route through the Flask test client | 9 of 19 pages returned **500** with `jinja2.exceptions.UndefinedError: 'labels' is undefined` / `'csrf_token' is undefined`. | Macros imported with `{% from "_macros.html" import … %}` do not receive the template context by default. | Import macros `with context`. | `test_every_get_route_leaves_the_database_unchanged` asserts every GET route returns 200/404/405, never 500. Browser runs also fail on any console error. | Fixed |

## Test-harness defects (bugs in test code, not the product)

| ID | Symptom | Cause | Fix |
|---|---|---|---|
| TH-01 | First full run: 17 failures with `TypeError: Lab.refused() got an unexpected keyword argument` | The helper `Lab.refused(code, fn, *args)` did not forward `**kwargs`. | It now forwards `**kwargs`. |
| TH-03 | During manual verification, the reviewer's case page looked identical to the anonymous one | Flask `session_transaction()` sets the cookie for `localhost`, but the probe sent `Host: 127.0.0.1`, so the session cookie was not sent back. The app was correct. | Tests use the default test-client host. |
| TH-02 | Two tests failed with `TypeError` inside convoluted conditional loops | An over-compact test loop passed arguments in the wrong shape. | The loops were rewritten as explicit calls. |
