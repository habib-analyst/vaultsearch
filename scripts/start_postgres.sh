#!/bin/bash
# Start the vaultsearch Postgres inside a user namespace.
# This VM's shell is uid 0 and the id map only covers uid 0 (chown to any
# other uid fails, and no usable non-root account exists). Postgres refuses
# to run as root, so the server runs in a user namespace where it sees
# itself as uid 1000 (mapped to host uid 0). Clients connect over TCP as
# usual; the net namespace is untouched.
PREFIX=$HOME/workspace/tools/pgsql
PGDATA=$HOME/workspace/tools/pgdata
PGSOCK=$HOME/workspace/tools/pgsock
mkdir -p "$PGSOCK"
chmod 700 "$PGDATA"

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  unshare -U --map-user=1000 --map-group=1000 \
    "$PREFIX/bin/initdb" -D "$PGDATA" -U postgres --auth=trust -E UTF8 --no-instructions
  cat >> "$PGDATA/postgresql.conf" <<EOF
port = 7432
listen_addresses = '127.0.0.1'
unix_socket_directories = '$PGSOCK'
shared_buffers = '256MB'
logging_collector = off
EOF
fi

nohup unshare -U --map-user=1000 --map-group=1000 \
  "$PREFIX/bin/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w start \
  >> "$PGDATA/pg_ctl.out" 2>&1 &
echo "pg start issued"
