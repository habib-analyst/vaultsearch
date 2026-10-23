#!/usr/bin/env python3
"""Bootstrap roles/databases and load the vaultsearch corpus into both
configurations (RLS and naive).

Run order per database:
  1. superuser creates the database (owned by vault_owner) and the vector ext
  2. vault_owner applies the schema (tables, indexes, policies, grants)
  3. vault_owner inserts tenants + documents, setting the tenant context per
     tenant batch — with FORCE ROW LEVEL SECURITY even the owner cannot
     insert a row for a tenant other than the one in context.
Embeddings come from the local all-MiniLM-L6-v2 model, loaded in-process.
Also plants one canary document per tenant (unique token in the body) that
the leakage suite greps for in other tenants' results.
"""
import json
import os
import sys

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "embed"))

HOST, PORT = "127.0.0.1", 7432
SUPER = dict(host=HOST, port=PORT, user="postgres", dbname="postgres")
OWNER_PW = "fixture_owner_password"
APP_PW = "fixture_app_password"

DBS = {"vaultsearch": "schema_rls.sql", "vaultsearch_naive": "schema_naive.sql"}


def super_conn(dbname="postgres"):
    return psycopg.connect(dbname=dbname, autocommit=True, **{k: v for k, v in SUPER.items() if k != "dbname"})


def owner_conn(dbname):
    return psycopg.connect(host=HOST, port=PORT, user="vault_owner",
                           password=OWNER_PW, dbname=dbname, autocommit=True)


def bootstrap_roles():
    with super_conn() as c:
        c.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vault_owner') THEN
            CREATE ROLE vault_owner LOGIN PASSWORD 'fixture_owner_password' NOSUPERUSER;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vault_app') THEN
            CREATE ROLE vault_app LOGIN PASSWORD 'fixture_app_password'
              NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
          END IF;
        END $$;""")


def main():
    from embed_sidecar import embed  # loads the model once

    docs = [json.loads(l) for l in open(os.path.join(ROOT, "corpus", "corpus.jsonl"))]
    tenants = sorted({d["tenant_id"] for d in docs})

    # canary docs: one per tenant, unique token, topic/cluster 'canary'
    for t in tenants:
        docs.append(dict(doc_id=f"{t[:4]}-canary", tenant_id=t, topic="canary",
                         cluster="canary", title=f"Canary record for {t}",
                         body=f"Internal canary token CANARY-{t.upper()}-7F3A. "
                              f"This record belongs only to {t} and must never "
                              f"appear in another tenant's results."))

    print(f"embedding {len(docs)} documents ...", flush=True)
    vectors = embed([d["title"] + " " + d["body"] for d in docs])

    bootstrap_roles()
    state = {}
    for dbname, schema_file in DBS.items():
        with super_conn() as c:
            exists = c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)).fetchone()
            if not exists:
                c.execute(f'CREATE DATABASE "{dbname}" OWNER vault_owner')
        with super_conn(dbname) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        schema = open(os.path.join(ROOT, "sql", schema_file)).read()
        schema = schema.replace("CREATE EXTENSION IF NOT EXISTS vector;", "-- ext created by loader")
        with owner_conn(dbname) as c:
            c.execute("DROP TABLE IF EXISTS documents CASCADE")
            c.execute("DROP TABLE IF EXISTS tenants CASCADE")
            c.execute(schema)
            for t in tenants:
                c.execute("INSERT INTO tenants VALUES (%s, %s) ON CONFLICT DO NOTHING",
                          (t, t))
            counts = {}
            for t in tenants:
                batch = [(d, v) for d, v in zip(docs, vectors) if d["tenant_id"] == t]
                # In the RLS database this context is what authorises the
                # owner's own inserts (FORCE RLS). In the naive DB it is unused.
                c.execute("SELECT set_config('app.tenant_id', %s, false)", (t,))
                with c.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO documents (doc_id, tenant_id, topic, cluster_, title, body, embedding)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(d["doc_id"], d["tenant_id"], d["topic"], d["cluster"],
                          d["title"], d["body"], "[" + ",".join(f"{x:.6f}" for x in v) + "]")
                         for d, v in batch])
                counts[t] = len(batch)
            c.execute("SELECT set_config('app.tenant_id', '', false)")
            total = c.execute("SELECT count(*) FROM documents").fetchone()[0] \
                if dbname.endswith("naive") else None
            state[dbname] = {"per_tenant_inserted": counts}
            print(f"{dbname}: inserted {counts}", flush=True)
    json.dump(state, open(os.path.join(ROOT, "loader", "load_state.json"), "w"), indent=2)
    print("LOAD_OK")


if __name__ == "__main__":
    main()
