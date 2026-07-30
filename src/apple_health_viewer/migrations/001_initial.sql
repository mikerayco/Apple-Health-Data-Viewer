CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE import_history (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_label TEXT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  parser_version TEXT,
  warning_count INTEGER NOT NULL DEFAULT 0,
  CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
  CHECK (source_kind IN ('zip_upload', 'folder_upload', 'configured_path'))
);
