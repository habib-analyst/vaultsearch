#!/usr/bin/env python3
"""Per-tenant ranking quality for vaultsearch.

For every labeled query (96: 4 tenants x 4 topics x 6), run the three arms
against the RLS database as the app role, inside that tenant's context:

  lexical — ts_rank over the tsvector; query stemmed to lexemes and
            OR-combined into a tsquery (the standard Postgres lexical
            baseline — AND-combined plainto_tsquery returns nothing for
            natural queries and is not a meaningful arm)
  vector  — exact cosine scan (index scans disabled for this session so
            the number is a pure ranking measurement, not an ANN recall
            measurement)
  fused   — Reciprocal Rank Fusion (k=60) over the two top-50 lists,
            the same SQL and fusion the Go service runs

Relevance grades come from the corpus's known structure:
  2 = same topic, 1 = sibling topic in the same cluster, 0 = else.
Metric: nDCG@10, reported globally, per tenant, per query style, and the
worst tenant per arm. Cross-tenant rows returned are also counted (must
be zero — the arms run under RLS here; quality and isolation share a run).
"""
import json
import math
import os
import sys

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "embed"))

APP = dict(host="127.0.0.1", port=7432, user="vault_app", password="fixture_app_password")
ARM = 50
K = 10


def dcg(rels):
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels))


def ndcg(ranked_ids, grades):
    rels = [grades.get(d, 0) for d in ranked_ids[:K]]
    ideal = sorted(grades.values(), reverse=True)[:K]
    idcg = dcg(ideal)
    return dcg(rels) / idcg if idcg else 0.0


def rrf(arms):
    scores = {}
    for arm in arms:
        for i, doc_id in enumerate(arm):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (60 + i + 1)
    return [d for d, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def main():
    from embed_sidecar import embed

    queries = [json.loads(l) for l in open(os.path.join(ROOT, "corpus", "queries.jsonl"))]
    docs = [json.loads(l) for l in open(os.path.join(ROOT, "corpus", "corpus.jsonl"))]
    grades_by_key = {}
    for q in queries:
        g = {}
        for d in docs:
            if d["tenant_id"] != q["tenant_id"]:
                continue
            if d["topic"] == q["topic"]:
                g[d["doc_id"]] = 2
            elif d["cluster"] == q["cluster"]:
                g[d["doc_id"]] = 1
        grades_by_key[(q["tenant_id"], q["query"])] = g

    qvecs = embed([q["query"] for q in queries])
    rows_out = []
    cross_tenant_hits = 0

    with psycopg.connect(dbname="vaultsearch", autocommit=True, **APP) as c:
        c.execute("SET enable_indexscan = off")  # exact vector scan for measurement purity
        for q, qv in zip(queries, qvecs):
            c.execute("SELECT set_config('app.tenant_id', %s, false)", (q["tenant_id"],))
            vec = "[" + ",".join(f"{x:.6f}" for x in qv) + "]"
            lex = [r[0] for r in c.execute(
                "SELECT d.doc_id FROM documents d,"
                " LATERAL (SELECT COALESCE(to_tsquery('english',"
                "   (SELECT string_agg(lx, ' | ')"
                "    FROM unnest(tsvector_to_array(to_tsvector('english', %s))) AS lx)),"
                "   ''::tsquery) AS tsq) q"
                " WHERE d.tsv @@ q.tsq ORDER BY ts_rank(d.tsv, q.tsq) DESC, d.doc_id LIMIT %s",
                (q["query"], ARM)).fetchall()]
            vecarm = [r[0] for r in c.execute(
                "SELECT doc_id FROM documents ORDER BY embedding <=> %s::vector, doc_id LIMIT %s",
                (vec, ARM)).fetchall()]
            fused = rrf([lex, vecarm])
            grades = grades_by_key[(q["tenant_id"], q["query"])]
            own = {d["doc_id"] for d in docs if d["tenant_id"] == q["tenant_id"]}
            for arm_ids in (lex, vecarm, fused):
                cross_tenant_hits += sum(1 for d in arm_ids if d not in own)
            rows_out.append(dict(
                tenant=q["tenant_id"], topic=q["topic"], style=q["style"], query=q["query"],
                lexical=ndcg(lex, grades), vector=ndcg(vecarm, grades), fused=ndcg(fused, grades)))

    def agg(rows, key):
        vals = [r[key] for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else None

    tenants = sorted({r["tenant"] for r in rows_out})
    summary = {
        "queries": len(rows_out),
        "cross_tenant_hits_in_eval": cross_tenant_hits,
        "global": {m: agg(rows_out, m) for m in ("lexical", "vector", "fused")},
        "per_tenant": {
            t: {m: agg([r for r in rows_out if r["tenant"] == t], m)
                for m in ("lexical", "vector", "fused")}
            for t in tenants},
        "per_style": {
            s: {m: agg([r for r in rows_out if r["style"] == s], m)
                for m in ("lexical", "vector", "fused")}
            for s in ("keyword", "paraphrase")},
    }
    summary["worst_tenant"] = {
        m: min(tenants, key=lambda t: summary["per_tenant"][t][m]) for m in ("lexical", "vector", "fused")}
    json.dump({"summary": summary, "per_query": rows_out},
              open(os.path.join(HERE, "ndcg_results.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
