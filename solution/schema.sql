PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, phone TEXT NOT NULL UNIQUE,
 role TEXT NOT NULL CHECK(role IN ('APPLICANT','SPECIALIST')), registeredAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expiresAt REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS challenges (
 id TEXT PRIMARY KEY, phone TEXT NOT NULL, mode TEXT NOT NULL,
 createdAt REAL NOT NULL, expiresAt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS applications (
 id TEXT PRIMARY KEY, applicantId TEXT NOT NULL REFERENCES users(id),
 status TEXT NOT NULL CHECK(status IN ('SUBMITTED','UNDER_REVIEW','NEEDS_SUPPLEMENT','COMPLETED')),
 submittedAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, applicationId TEXT NOT NULL REFERENCES applications(id),
 requestId TEXT UNIQUE REFERENCES supplement_requests(id), number INTEGER NOT NULL CHECK(number > 0),
 questionnaire TEXT NOT NULL, submittedAt TEXT NOT NULL, UNIQUE(applicationId,number)
);
CREATE TABLE IF NOT EXISTS checklist_items (
 id TEXT PRIMARY KEY, versionId TEXT NOT NULL REFERENCES versions(id),
 code TEXT NOT NULL CHECK(code IN ('5.1','5.2','5.3')), requiredAction TEXT NOT NULL,
 basis TEXT NOT NULL, UNIQUE(versionId,code), UNIQUE(id,versionId)
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, versionId TEXT NOT NULL REFERENCES versions(id), itemId TEXT NOT NULL,
 kind TEXT NOT NULL, fileName TEXT NOT NULL, mediaType TEXT NOT NULL,
 content BLOB NOT NULL, attachedAt TEXT NOT NULL,
 FOREIGN KEY(itemId,versionId) REFERENCES checklist_items(id,versionId)
);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, versionId TEXT NOT NULL REFERENCES versions(id),
 specialistId TEXT NOT NULL REFERENCES users(id),
 state TEXT NOT NULL CHECK(state IN ('OPEN','ASSESSED','SUPPLEMENT_REQUESTED','COMPLETED')),
 startedAt TEXT NOT NULL, assessedAt TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_review ON reviews(versionId)
 WHERE state IN ('OPEN','ASSESSED');
CREATE TABLE IF NOT EXISTS findings (
 id TEXT PRIMARY KEY, reviewId TEXT NOT NULL REFERENCES reviews(id),
 itemId TEXT NOT NULL REFERENCES checklist_items(id),
 assessment TEXT NOT NULL CHECK(assessment IN ('CONFORMS','NEEDS_CORRECTION')),
 comment TEXT NOT NULL, UNIQUE(reviewId,itemId)
);
CREATE TABLE IF NOT EXISTS supplement_requests (
 id TEXT PRIMARY KEY, reviewId TEXT NOT NULL UNIQUE REFERENCES reviews(id),
 instructions TEXT NOT NULL, requestedAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conclusions (
 id TEXT PRIMARY KEY, reviewId TEXT NOT NULL UNIQUE REFERENCES reviews(id),
 applicationId TEXT NOT NULL UNIQUE REFERENCES applications(id),
 conclusionText TEXT NOT NULL, issuedAt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_events (
 id TEXT PRIMARY KEY, applicationId TEXT NOT NULL REFERENCES applications(id),
 initiatorId TEXT NOT NULL REFERENCES users(id), number INTEGER NOT NULL,
 previousStatus TEXT, newStatus TEXT NOT NULL, occurredAt TEXT NOT NULL,
 comment TEXT NOT NULL, UNIQUE(applicationId,number)
);
CREATE INDEX IF NOT EXISTS applications_by_owner ON applications(applicantId);
CREATE TRIGGER IF NOT EXISTS versions_immutable BEFORE UPDATE ON versions BEGIN
 SELECT RAISE(ABORT, 'Accepted versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS documents_immutable BEFORE UPDATE ON documents BEGIN
 SELECT RAISE(ABORT, 'Accepted documents are immutable'); END;
CREATE TRIGGER IF NOT EXISTS checklist_immutable BEFORE UPDATE ON checklist_items BEGIN
 SELECT RAISE(ABORT, 'Accepted checklists are immutable'); END;
CREATE TRIGGER IF NOT EXISTS history_immutable BEFORE UPDATE ON status_events BEGIN
 SELECT RAISE(ABORT, 'History is immutable'); END;
CREATE TRIGGER IF NOT EXISTS mutually_exclusive_request BEFORE INSERT ON supplement_requests
 WHEN EXISTS(SELECT 1 FROM conclusions WHERE reviewId=NEW.reviewId) BEGIN
 SELECT RAISE(ABORT, 'Review already has a conclusion'); END;
CREATE TRIGGER IF NOT EXISTS mutually_exclusive_conclusion BEFORE INSERT ON conclusions
 WHEN EXISTS(SELECT 1 FROM supplement_requests WHERE reviewId=NEW.reviewId) BEGIN
 SELECT RAISE(ABORT, 'Review already has a supplement request'); END;
