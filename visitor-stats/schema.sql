-- Schema snapshot. Initial migration: migrations/0001_initial.sql.
CREATE TABLE IF NOT EXISTS visitor_totals (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  started_at TEXT,
  unmapped_pageviews INTEGER NOT NULL DEFAULT 0 CHECK (unmapped_pageviews >= 0)
);

INSERT INTO visitor_totals (singleton, started_at, unmapped_pageviews)
VALUES (1, NULL, 0)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS visitor_cities (
  cc TEXT NOT NULL CHECK (length(cc) = 2),
  region TEXT NOT NULL COLLATE NOCASE,
  city TEXT NOT NULL COLLATE NOCASE CHECK (length(city) > 0),
  lat REAL NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lon REAL NOT NULL CHECK (lon BETWEEN -180 AND 180),
  n INTEGER NOT NULL CHECK (n > 0),
  PRIMARY KEY (cc, region, city)
) WITHOUT ROWID;
