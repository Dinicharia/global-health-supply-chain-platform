-- =============================================================================
-- Postgres first-boot initialization: create auxiliary databases
-- =============================================================================
-- Purpose: creates the "prefect" and "superset" databases automatically
--          on a FRESH volume's first boot -- this file is placed in
--          /docker-entrypoint-initdb.d/, which the official postgres
--          image only executes ONCE, when the data directory is empty.
--          (We created the "prefect" database manually earlier in this
--          project, before this file existed -- this formalizes that
--          same step for anyone spinning up a genuinely fresh volume.)
CREATE DATABASE prefect;
CREATE DATABASE superset;