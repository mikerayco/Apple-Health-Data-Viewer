ALTER TABLE import_history ADD COLUMN job_id TEXT;
ALTER TABLE import_history ADD COLUMN source_id TEXT;
ALTER TABLE import_history ADD COLUMN record_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_history ADD COLUMN workout_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_history ADD COLUMN duplicate_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_history ADD COLUMN health_database_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE import_history ADD COLUMN export_date TEXT;
ALTER TABLE import_history ADD COLUMN error_code TEXT;

CREATE UNIQUE INDEX import_history_job_id_idx ON import_history(job_id);

CREATE TABLE managed_sources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (kind IN ('zip_upload', 'folder_upload')),
  CHECK (size_bytes >= 0)
);
