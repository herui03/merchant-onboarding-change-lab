-- Merchant Onboarding Policy Change Lab — SQLite schema (synthetic data only).
-- Defence in depth: the service layer enforces every rule; these triggers make the most
-- important ones hold even for raw SQL (immutable history, no status bypass).

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE actor (
  id           TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  title        TEXT NOT NULL,
  roles_json   TEXT NOT NULL
);

CREATE TABLE policy_version (
  version        TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  effective_from TEXT NOT NULL UNIQUE,
  change_request TEXT NOT NULL,
  summary        TEXT NOT NULL,
  rules_json     TEXT NOT NULL,
  rules_sha256   TEXT NOT NULL
);

CREATE TABLE application (
  id                     INTEGER PRIMARY KEY,
  reference              TEXT NOT NULL UNIQUE,
  business_identifier    TEXT NOT NULL UNIQUE,
  status                 TEXT NOT NULL CHECK (status IN ('draft','submitted','needs_information','approved','rejected')),
  current_revision_id    INTEGER REFERENCES application_revision(id),
  cycle                  INTEGER NOT NULL DEFAULT 1,
  row_version            INTEGER NOT NULL DEFAULT 1,
  created_by             TEXT NOT NULL REFERENCES actor(id),
  created_on             TEXT NOT NULL,
  created_at             TEXT NOT NULL,
  -- Working draft (editable only while status is draft or needs_information).
  draft_legal_name       TEXT NOT NULL,
  draft_product_category TEXT NOT NULL,
  draft_volume_band      TEXT NOT NULL,
  draft_activity_summary TEXT NOT NULL DEFAULT ''
);

-- AUTOINCREMENT: draft documents are the only deletable rows, and a plain INTEGER PRIMARY KEY
-- lets SQLite reissue a deleted max id, so a stale "remove id N" could delete a replacement
-- (DEF-002, Codex R3-02). AUTOINCREMENT ids are never reused.
CREATE TABLE draft_document (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id INTEGER NOT NULL REFERENCES application(id),
  doc_type       TEXT NOT NULL,
  title          TEXT NOT NULL,
  issued_on      TEXT NOT NULL,
  page_count     INTEGER NOT NULL,
  reference      TEXT NOT NULL,
  added_by       TEXT NOT NULL REFERENCES actor(id),
  added_on       TEXT NOT NULL,
  UNIQUE (application_id, reference)
);

CREATE TABLE application_revision (
  id                  INTEGER PRIMARY KEY,
  application_id      INTEGER NOT NULL REFERENCES application(id),
  revision_no         INTEGER NOT NULL,
  cycle               INTEGER NOT NULL,
  legal_name          TEXT NOT NULL,
  business_identifier TEXT NOT NULL,
  product_category    TEXT NOT NULL,
  volume_band         TEXT NOT NULL,
  activity_summary    TEXT NOT NULL,
  response_note       TEXT NOT NULL,
  submitted_by        TEXT NOT NULL REFERENCES actor(id),
  submitted_on        TEXT NOT NULL,
  submitted_at        TEXT NOT NULL,
  content_sha256      TEXT NOT NULL,
  UNIQUE (application_id, revision_no)
);

CREATE TABLE revision_document (
  id          INTEGER PRIMARY KEY,
  revision_id INTEGER NOT NULL REFERENCES application_revision(id),
  position    INTEGER NOT NULL,
  doc_type    TEXT NOT NULL,
  title       TEXT NOT NULL,
  issued_on   TEXT NOT NULL,
  page_count  INTEGER NOT NULL,
  reference   TEXT NOT NULL,
  UNIQUE (revision_id, position)
);

CREATE TABLE migration_run (
  id             INTEGER PRIMARY KEY,
  target_version TEXT NOT NULL REFERENCES policy_version(version),
  run_by         TEXT NOT NULL REFERENCES actor(id),
  run_on         TEXT NOT NULL,
  run_at         TEXT NOT NULL,
  summary_json   TEXT NOT NULL
);

CREATE TABLE policy_evaluation (
  id                      INTEGER PRIMARY KEY,
  revision_id             INTEGER NOT NULL REFERENCES application_revision(id),
  policy_version          TEXT NOT NULL REFERENCES policy_version(version),
  trigger                 TEXT NOT NULL CHECK (trigger IN ('submission','migration')),
  migration_run_id        INTEGER REFERENCES migration_run(id),
  reference_date          TEXT NOT NULL,
  evaluated_on            TEXT NOT NULL,
  evaluated_at            TEXT NOT NULL,
  revision_content_sha256 TEXT NOT NULL,
  complete                INTEGER NOT NULL CHECK (complete IN (0,1)),
  result_json             TEXT NOT NULL,
  result_sha256           TEXT NOT NULL,
  UNIQUE (revision_id, policy_version)
);

CREATE TABLE migration_item (
  id                   INTEGER PRIMARY KEY,
  run_id               INTEGER NOT NULL REFERENCES migration_run(id),
  application_id       INTEGER NOT NULL REFERENCES application(id),
  revision_id          INTEGER REFERENCES application_revision(id),
  classification       TEXT NOT NULL,
  status_before        TEXT NOT NULL,
  status_after         TEXT NOT NULL,
  prior_policy_version TEXT NOT NULL,
  evaluation_id        INTEGER REFERENCES policy_evaluation(id),
  new_gaps_json        TEXT NOT NULL,
  target_unmet_json    TEXT NOT NULL
);

CREATE TABLE info_request (
  id                       INTEGER PRIMARY KEY,
  application_id           INTEGER NOT NULL REFERENCES application(id),
  revision_id              INTEGER NOT NULL REFERENCES application_revision(id),
  source                   TEXT NOT NULL CHECK (source IN ('reviewer','policy_migration')),
  requested_by             TEXT NOT NULL REFERENCES actor(id),
  reason_code              TEXT NOT NULL,
  message                  TEXT NOT NULL,
  requested_doc_types_json TEXT NOT NULL,
  migration_run_id         INTEGER REFERENCES migration_run(id),
  requested_on             TEXT NOT NULL,
  requested_at             TEXT NOT NULL
);

CREATE TABLE decision (
  id              INTEGER PRIMARY KEY,
  application_id  INTEGER NOT NULL REFERENCES application(id),
  revision_id     INTEGER NOT NULL UNIQUE REFERENCES application_revision(id),
  evaluation_id   INTEGER NOT NULL REFERENCES policy_evaluation(id),
  policy_version  TEXT NOT NULL REFERENCES policy_version(version),
  outcome         TEXT NOT NULL CHECK (outcome IN ('approved','rejected')),
  reason_code     TEXT NOT NULL,
  reason_text     TEXT NOT NULL,
  decided_by      TEXT NOT NULL REFERENCES actor(id),
  decided_on      TEXT NOT NULL,
  decided_at      TEXT NOT NULL,
  snapshot_json   TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL
);
-- Approved applications cannot be reopened, so an application is approved at most once.
CREATE UNIQUE INDEX decision_one_approval_per_application ON decision(application_id) WHERE outcome = 'approved';

CREATE TABLE event (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER REFERENCES application(id),
  revision_id    INTEGER REFERENCES application_revision(id),
  event_type     TEXT NOT NULL,
  actor_id       TEXT REFERENCES actor(id),
  business_date  TEXT NOT NULL,
  recorded_at    TEXT NOT NULL,
  reason_code    TEXT NOT NULL DEFAULT '',
  message        TEXT NOT NULL DEFAULT '',
  details_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX event_by_application ON event(application_id, id);

CREATE TABLE idempotency_record (
  request_key     TEXT PRIMARY KEY,
  actor_id        TEXT NOT NULL REFERENCES actor(id),
  operation       TEXT NOT NULL,
  application_ref TEXT NOT NULL,
  revision_id     INTEGER,
  policy_version  TEXT NOT NULL,
  evaluation_id   INTEGER,
  payload_sha256  TEXT NOT NULL,
  binding_sha256  TEXT NOT NULL,
  http_status     INTEGER NOT NULL,
  response_json   TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Status machine at the database level (REQ-WF-01/02): raw UPDATEs cannot bypass it.
-- ---------------------------------------------------------------------------
CREATE TRIGGER application_status_transition
BEFORE UPDATE OF status ON application
WHEN OLD.status <> NEW.status AND NOT (
     (OLD.status = 'draft'             AND NEW.status = 'submitted')
  OR (OLD.status = 'submitted'         AND NEW.status IN ('needs_information','approved','rejected'))
  OR (OLD.status = 'needs_information' AND NEW.status = 'submitted')
  OR (OLD.status = 'rejected'          AND NEW.status = 'draft')
)
BEGIN
  SELECT RAISE(ABORT, 'INVALID_TRANSITION: status change not allowed');
END;

CREATE TRIGGER application_terminal_requires_decision
BEFORE UPDATE OF status ON application
WHEN NEW.status IN ('approved','rejected') AND OLD.status <> NEW.status AND NOT EXISTS (
  SELECT 1 FROM decision d WHERE d.revision_id = NEW.current_revision_id AND d.outcome = NEW.status
)
BEGIN
  SELECT RAISE(ABORT, 'TERMINAL_STATUS_REQUIRES_DECISION');
END;

CREATE TRIGGER application_identity_immutable
BEFORE UPDATE OF reference, business_identifier, created_by, created_on, created_at ON application
BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE: application identity cannot change');
END;

CREATE TRIGGER application_draft_locked
BEFORE UPDATE OF draft_legal_name, draft_product_category, draft_volume_band, draft_activity_summary ON application
WHEN OLD.status NOT IN ('draft','needs_information')
BEGIN
  SELECT RAISE(ABORT, 'DRAFT_LOCKED: draft can only change in draft or needs_information');
END;

CREATE TRIGGER application_no_delete BEFORE DELETE ON application
BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE: applications cannot be deleted');
END;

CREATE TRIGGER draft_document_locked_insert BEFORE INSERT ON draft_document
WHEN (SELECT status FROM application WHERE id = NEW.application_id) NOT IN ('draft','needs_information')
BEGIN
  SELECT RAISE(ABORT, 'DRAFT_LOCKED: evidence can only change in draft or needs_information');
END;

CREATE TRIGGER draft_document_locked_delete BEFORE DELETE ON draft_document
WHEN (SELECT status FROM application WHERE id = OLD.application_id) NOT IN ('draft','needs_information')
BEGIN
  SELECT RAISE(ABORT, 'DRAFT_LOCKED: evidence can only change in draft or needs_information');
END;

CREATE TRIGGER draft_document_no_update BEFORE UPDATE ON draft_document
BEGIN
  SELECT RAISE(ABORT, 'IMMUTABLE: remove and re-add a draft document instead of editing it');
END;
