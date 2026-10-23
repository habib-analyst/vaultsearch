#!/usr/bin/env python3
"""Merge the leakage suite, API probes and nDCG eval into results.json."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(p):
    return json.load(open(os.path.join(ROOT, p)))


sql = load("attacks/leakage_results.json")
api = load("attacks/api_results.json")
ndcg = load("eval/ndcg_results.json")["summary"]


def summarise(rows):
    by_config = {}
    for r in rows:
        c = by_config.setdefault(r["config"], {"attempts": 0, "leaks": 0})
        c["attempts"] += 1
        c["leaks"] += 1 if r["leaked"] else 0
    return by_config


results = {
    "environment": {
        "postgres": "PostgreSQL 16.4 (built from source, --without-readline --without-zlib)",
        "pgvector": "0.8.0 (built from source against the local install)",
        "server": "127.0.0.1:7432, runs as uid 1000 inside a user namespace (the VM shell is root; Postgres refuses root)",
        "embedding_model": "all-MiniLM-L6-v2 (384-dim, local, sentence-transformers)",
        "service": "Go 1.23, pgx/v5, stdlib HTTP; lexical arm = ts_rank over OR-combined stemmed tsquery; vector arm = pgvector cosine; fusion = RRF k=60",
        "corpus": "1200 synthetic documents (4 tenants x 4 topics x 75) + 4 canary documents = 1204 per database",
    },
    "leakage_sql_surface": {
        "summary_by_config": summarise(sql),
        "attempt_log": sql,
    },
    "leakage_api_surface": {
        "summary_by_config": summarise(api),
        "attempt_log": api,
    },
    "ndcg_at_10": ndcg,
    "headline": {
        "rls_config_leaks": sum(r["leaked"] for r in sql + api if r["config"] in ("vaultsearch", "rls")),
        "rls_config_attempts": sum(1 for r in sql + api if r["config"] in ("vaultsearch", "rls")),
        "naive_config_leaks": sum(r["leaked"] for r in sql + api if r["config"] in ("vaultsearch_naive", "naive")),
        "naive_config_attempts": sum(1 for r in sql + api if r["config"] in ("vaultsearch_naive", "naive")),
    },
}
json.dump(results, open(os.path.join(ROOT, "results.json"), "w"), indent=2)
print(json.dumps(results["headline"], indent=2))
print("sql:", summarise(sql))
print("api:", summarise(api))
