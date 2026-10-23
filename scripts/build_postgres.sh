#!/bin/bash
# Build Postgres 16.4 + pgvector 0.8.0 from source into ~/workspace/tools/pgsql.
# Compile + install only; start the server with scripts/start_postgres.sh.
set -x
export no_proxy='localhost,127.0.0.1,::1'
export NO_PROXY='localhost,127.0.0.1,::1'

TOOLS=$HOME/workspace/tools
SRC=$TOOLS/pgsql-src
TARB=$TOOLS/src-tarballs
PREFIX=$TOOLS/pgsql
PGDATA=$TOOLS/pgdata
PGSOCK=$TOOLS/pgsock
mkdir -p "$SRC" "$TARB" "$PGSOCK"

cd "$TARB"
[ -f postgresql-16.4.tar.gz ] || curl -fL -o postgresql-16.4.tar.gz https://ftp.postgresql.org/pub/source/v16.4/postgresql-16.4.tar.gz
[ -f pgvector-0.8.0.tar.gz ] || curl -fL -o pgvector-0.8.0.tar.gz https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.0.tar.gz \
  || curl -fL -o pgvector-0.8.0.tar.gz https://codeload.github.com/pgvector/pgvector/tar.gz/refs/tags/v0.8.0
ls -la

cd "$SRC"
[ -d postgresql-16.4 ] || tar xzf "$TARB/postgresql-16.4.tar.gz"
[ -d pgvector-0.8.0 ] || tar xzf "$TARB/pgvector-0.8.0.tar.gz"

cd "$SRC/postgresql-16.4"
./configure --prefix="$PREFIX" --without-readline --without-zlib || { echo CONFIGURE_FAILED; exit 1; }
make -j2 || { echo MAKE_FAILED; exit 1; }
make install || { echo INSTALL_FAILED; exit 1; }
"$PREFIX/bin/postgres" --version

cd "$SRC/pgvector-0.8.0"
export PG_CONFIG=$PREFIX/bin/pg_config
make || { echo PGVECTOR_MAKE_FAILED; exit 1; }
make install || { echo PGVECTOR_INSTALL_FAILED; exit 1; }
ls -la "$PREFIX/lib/vector."* "$PREFIX/share/extension/vector"* | head

echo "=== BUILD_OK ==="
