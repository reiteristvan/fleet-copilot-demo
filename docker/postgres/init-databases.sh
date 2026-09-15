#!/bin/bash
# Runs once, on an empty data directory.
#
# Langfuse gets its own database on the same server rather than its own
# container: it is a second schema's worth of load in local development, and a
# second postgres would cost another ~150MB of RAM for no isolation we need.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE EXTENSION IF NOT EXISTS vector;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
  CREATE DATABASE ${LANGFUSE_DB_NAME};
SQL
