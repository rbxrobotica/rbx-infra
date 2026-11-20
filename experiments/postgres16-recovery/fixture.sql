BEGIN;
CREATE EXTENSION hstore WITH SCHEMA public;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SET LOCAL ROLE lab_owner;
CREATE SCHEMA sample;
CREATE TABLE sample.categories (id integer PRIMARY KEY, label text NOT NULL UNIQUE);
CREATE TABLE sample.entries (
    id integer PRIMARY KEY,
    category_id integer NOT NULL REFERENCES sample.categories (id),
    label text NOT NULL,
    recorded_at timestamptz NOT NULL,
    amount numeric(12,2) NOT NULL CHECK (amount > 0),
    payload bytea NOT NULL,
    properties public.hstore NOT NULL
);
CREATE INDEX entries_recorded_at ON sample.entries (recorded_at);
INSERT INTO sample.categories VALUES (1, 'café'), (2, '東京');
INSERT INTO sample.entries
SELECT id, 1 + id % 2, 'ação 東京 ' || id,
       '2026-11-19 12:00:00+00'::timestamptz + id * interval '1 second',
       (id * 1.23)::numeric(12,2), decode(repeat(md5(id::text), 64), 'hex'),
       public.hstore('fixture', 'sintético')
FROM generate_series(1, 2400) AS id;
GRANT USAGE ON SCHEMA sample TO lab_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sample TO lab_reader;
COMMIT;
