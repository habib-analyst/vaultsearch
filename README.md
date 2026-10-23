# vaultsearch

Multi-tenant hybrid search on **real Postgres** — Row-Level Security +
pgvector + lexical ranking fused into one query path — shipped with the
two measurements hybrid-search demos never include:

1. an **adversarial cross-tenant leakage suite** that actively tries to
   break tenant isolation and publishes its full attempt log, run against
   both the RLS schema and the identical schema with application-filter
   isolation only;
2. **per-tenant ranking quality** (nDCG@10) for the lexical, vector and
   fused arms — because a global average can hide a tenant the fusion
   fails.

## Headline results (measured, this machine)

| | RLS configuration | Naive configuration (app filter only) |
|---|---|---|
| SQL-surface attacks leaked | **0 / 8** | **6 / 8** |
| API-surface probes leaked | **0 / 8** | **1 / 8** |

nDCG@10 over 96 labeled queries (exact vector scan; 0 cross-tenant hits
in any arm):

| arm | global | worst tenant | best single arm flips by tenant |
|---|---|---|---|
| lexical (ts_rank) | 0.9972 | 0.9932 (benco-auto) | wins acme-cooking, ledgerly-finance |
| vector (cosine) | 0.9925 | 0.9845 (acme-cooking) | wins benco-auto, verdant-garden |
| **fused (RRF k=60)** | **0.9996** | **0.9982 (ledgerly-finance)** | never the worst arm for any tenant |

Full attempt log, per-query scores and method: `PAPER.md` and
`results.json`.

## Design

- One Go service binary, two deployment modes. In `rls` mode the SQL
  contains **no tenant predicate at all**: each request runs in a
  transaction that sets `app.tenant_id` from the authenticated API key,
  and a `FORCE ROW LEVEL SECURITY` policy does the isolation. The app
  role is neither owner nor superuser and has no `BYPASSRLS`.
- In `naive` mode the database is identical minus RLS, and the service
  adds `AND tenant_id = …` to its primary paths — except `/similar`,
  which was ported from the RLS codebase and carries no filter. That
  asymmetry is deliberate and documented: it is the standard way real
  multi-tenant leaks happen (a later query path that forgets), and the
  suite prices it.
- Lexical arm: query stemmed to lexemes, OR-combined into a `tsquery`,
  ranked with `ts_rank`. (AND-combined `plainto_tsquery` is a degenerate
  arm for natural queries — it requires meta-words like "guide" to
  appear in documents and returns nothing; it is not used.)
- Vector arm: pgvector cosine over all-MiniLM-L6-v2 embeddings (384-dim,
  computed locally). A shared HNSW index spans all tenants; the leakage
  suite probes it directly for nearest-neighbour bleed.
- Fusion: Reciprocal Rank Fusion (k=60) over the two top-50 lists.

## Layout

```
sql/        schema_rls.sql, schema_naive.sql (identical but for RLS)
corpus/     gen_corpus.py -> corpus.jsonl (1200 docs + queries, labeled)
loader/     load.py — roles, databases, embeddings, canary docs
embed/      embed_sidecar.py — local embedding HTTP service (:7433)
service/    Go API (pgx): /search, /documents/{id}, /similar/{id}
attacks/    leakage_suite.py (SQL surface), api_probes.py (HTTP surface)
eval/       eval_ndcg.py, build_results.py -> ../results.json
run_tests.sh, run_services.sh
```

## Run it

```bash
# 1. Postgres 16.4 + pgvector 0.8.0 from source (see PAPER.md §Setup for
#    the exact commands, incl. the user-namespace step this VM needs)
bash scripts/build_postgres.sh   # compile + install into ~/workspace/tools/pgsql
bash scripts/start_postgres.sh   # initdb (first run) + start :7432

# 2. Load both databases
python3 loader/load.py

# 3. Build and run the service (both deployments) + embedding sidecar
(cd service && go build -o ../bin/vaultsearch .)
bash run_services.sh                    # rls :7443, naive :7444

# 4. Test
bash run_tests.sh
```

API keys are obvious fixtures (`fixture-key-<tenant>`); the local DB app
password is `fixture_app_password`. Nothing here is a real credential.

## Honest limits

- Synthetic corpus and template-generated queries. Paraphrase queries
  retain the topic name, so absolute nDCG values are near ceiling and
  optimistic; the per-arm / per-tenant *comparisons* are the signal,
  not the absolute values.
- Single node, 1,204 documents per database; the vector arm is measured
  with an exact scan (ANN recall is a separate question the leakage
  suite touches but the ranking eval deliberately excludes).
- The timing probe (A7) found a small median gap (0.073 ms vs 0.051 ms,
  ratio 1.43 at 300 reps) — under the pre-set 1.5× distinguishability
  rule, so recorded as blocked, but the raw numbers are in the log; a
  patient attacker with far more samples might resolve a signal there.
- Threat model: the attacker controls SQL run as the app role with
  their own tenant context (or none). Voluntarily setting the context
  to the victim is out of scope — in this design the context *is* the
  credential and is set only from the authenticated session.
