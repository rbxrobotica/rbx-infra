SELECT row_to_json(s) FROM (
    SELECT current_database() AS database, pg_encoding_to_char(d.encoding) AS encoding,
           d.datcollate, d.datctype, pg_get_userbyid(d.datdba) AS owner,
           has_database_privilege('lab_reader', d.oid, 'CONNECT') AS reader_connect,
           has_database_privilege('lab_reader', d.oid, 'CREATE') AS reader_create
    FROM pg_database d WHERE d.datname = current_database()
) s;
SELECT row_to_json(s) FROM (
    SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
           rolreplication, rolbypassrls
    FROM pg_roles WHERE rolname IN ('lab_owner', 'lab_reader') ORDER BY rolname
) s;
SELECT row_to_json(s) FROM (
    SELECT extname, extversion, nspname, pg_get_userbyid(extowner) AS owner
    FROM pg_extension JOIN pg_namespace ON extnamespace = pg_namespace.oid
    ORDER BY extname
) s;
SELECT row_to_json(s) FROM (
    SELECT count(*) AS entries, min(amount)::text AS minimum, max(amount)::text AS maximum,
           sum(octet_length(payload)) AS payload_bytes
    FROM sample.entries
) s;
