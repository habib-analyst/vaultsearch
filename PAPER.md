# vaultsearch: hybrid search with a measured tenant boundary

## 1. Position

Tutorials demo hybrid search *or* multi-tenancy. Security write-ups
advise RLS for pgvector stores; ranking benchmarks report one global
nDCG for one tenant-less corpus; the closest prior art on isolation
testing is a blog post with pytest snippets for vector databases in
general. What does not exist as a packaged artifact is the pair
measured together: an adversarial leakage suite with a published
attempt log (against RLS **and** against app-filter-only isolation on
the identical schema), and ranking quality broken down per tenant.
vaultsearch is that measurement artifact: a working hybrid search
service on real Postgres, plus the evidence about its boundary and
its ranking. (See `KILLGATE.md` for the prior-art review.)

## 2. Setup (exact)

PostgreSQL 16.4 from source on this VM (2 cores, 7 GB RAM):

```
curl -fL https://ftp.postgresql.org/pub/source/v16.4/postgresql-16.4.tar.gz
tar xzf postgresql-16.4.tar.gz && cd postgresql-16.4
./configure --prefix=$HOME/workspace/tools/pgsql --without-readline --without-zlib
make -j2 && make install
curl -fL https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.0.tar.gz
tar xzf v0.8.0.tar.gz && cd pgvector-0.8.0
PG_CONFIG=$HOME/workspace/tools/pgsql/bin/pg_config make && make install
```

This VM's shell is root and its uid map covers only uid 0 (chown to
any other uid fails; there is no usable service account), and Postgres
refuses to run as root. The server therefore runs inside a user
namespace (`unshare -U --map-user=1000 --map-group=1000`, with a
`pguser` passwd entry for uid 1000), listening on 127.0.0.1:7432 with
the data dir under `~/workspace/tools/pgdata`. Scripts:
`~/workspace/tools/build_pgsql.sh`, `~/workspace/tools/start_pg.sh`.

Schema (`sql/schema_rls.sql`): `documents(doc_id, tenant_id, topic,
cluster_, title, body, tsv generated, embedding vector(384))`, GIN
index on `tsv`, one **shared HNSW index** on `embedding` across all
tenants. `ENABLE` + `FORCE ROW LEVEL SECURITY`; policy
`tenant_id = current_setting('app.tenant_id', true)` for USING and
WITH CHECK. Roles: `vault_owner` (owns the tables) and `vault_app`
(LOGIN, NOSUPERUSER, NOBYPASSRLS — the role the service and the
attacker both use). The naive database (`sql/schema_naive.sql`) is
byte-identical except the RLS block is absent and no policy exists.

Corpus: 4 tenants × 4 topics × 75 documents = 1,200, plus one canary
document per tenant carrying a unique token (`CANARY-<TENANT>-7F3A`),
1,204 per database. Tenants are distinct domains (cooking, auto
repair, gardening, personal finance) so bleed is obvious. Embeddings:
all-MiniLM-L6-v2, computed locally; even the owner's inserts run under
a per-tenant context, because FORCE RLS binds the owner too.

Service: Go + pgx, stdlib HTTP. Tenant identity comes only from the
API key; there is no tenant parameter anywhere in the API. In RLS
mode the search SQL has no tenant predicate. In naive mode the
service adds `AND tenant_id = …` to `/search` and `/documents/{id}`;
`/similar` carries no filter in either mode (in RLS mode it needs
none) — the documented retrofit gap.

## 3. Leakage results

Attacker: tenant A (acme-cooking) against victim B (benco-auto).
SQL surface = SQL executed as `vault_app` with A's context (or none).
Full log: `results.json` (`attacks/leakage_results.json`,
`attacks/api_results.json`).

### SQL surface

| # | attack | RLS | naive |
|---|---|---|---|
| A1 | unscoped SELECT, no tenant context | blocked (0 rows) | **LEAK — all 1,204 rows visible** |
| A2 | context=A + crafted predicates (`tenant_id='B'`, OR, direct id) | blocked (0 foreign) | **LEAK — 602 foreign rows** |
| A3-ann | NN probe seeded with B's own text, via shared HNSW index | blocked (0/10 foreign) | **LEAK — 10/10 neighbours are B's** |
| A3-exact | same probe, exact scan | blocked (0/10 foreign) | **LEAK — 10/10 neighbours are B's** |
| A4 | error oracle: `1/(count of id)` distinguishes B's doc from a nonexistent id? | blocked (identical behaviour) | **LEAK — B's doc errors differently** |
| A5 | privilege escalation ×5 (SET ROLE owner, DISABLE RLS, DROP POLICY, CREATE EXTENSION, COPY TO file) | blocked (0/5) | blocked (0/5) |
| A6 | cross-tenant writes (insert into B, update B's row) | blocked | **LEAK — insert ok, update rowcount 1** |
| A7 | timing: point lookup of B's id vs nonexistent id, medians over 300 reps | blocked — 0.073 ms vs 0.051 ms, ratio 1.43 (< 1.5× pre-set rule) | blocked — ratio 1.16 (moot: rows are directly readable) |

Notes on the interesting rows:

- **A3 is the pgvector-specific fear**: candidate generation inside a
  shared ANN index happens before heap filtering, so a poisoned or
  careless stack could bleed neighbours. Here the index returns
  candidates and RLS filters the heap rows: under A the result is
  A's own nearest documents, 0 foreign in top-10 by both ANN and
  exact scan. The failure mode to watch is under-fill, not leakage.
- **A4 shows the oracle dies with visibility**: in the naive schema
  the error behaviour of `1/(count…)` is a clean existence oracle
  for any tenant's document ids; under RLS an existing-but-foreign
  id is indistinguishable from a nonexistent one.
- **A5 blocks in both** because it tests the role, not the policy:
  correct grants matter independently of RLS.
- **A7 is reported with its raw numbers, not a clean zero.** A
  22 µs median gap at 300 reps sits under the pre-registered 1.5×
  distinguishability threshold; it is the one channel where the
  honest statement is "not resolved at this sample size", not
  "provably absent".

### API surface (attacker holds A's API key)

| # | probe | RLS deployment | naive deployment |
|---|---|---|---|
| P1 | search B's domain terms, all 3 modes | blocked | blocked (app filter) |
| P2 | GET /documents/{B id} | blocked (404) | blocked (404, app filter) |
| P3 | GET /similar/{B id} | blocked (404 — seed invisible) | **LEAK — B's neighbours returned** |
| P4 | GET /similar/{A id} neighbour scope | blocked | blocked — top-10 happen to be A's own (semantically nearest); the path is unscoped, as P3 proves |
| P5 | tenant_id in body + X-Tenant-Id header | blocked (ignored) | blocked (ignored) |
| P6 | malformed inputs ×5 (string k, SQL syntax in query, bad mode, oversized k, empty query) | blocked | blocked |
| P7 | canary hunt, 12 searches incl. the literal token | blocked | blocked |
| P8 | missing / wrong API key | blocked (401/401) | blocked (401/401) |

**Totals: RLS configuration 0 leaks / 16 attempts. Naive
configuration 7 leaks / 16 attempts.**

The naive deployment's API mostly holds — its application filter is
correct on the paths that have one. It fails exactly where the threat
model said it would: at the SQL surface (any present/future code path
or injection foothold inherits nothing) and on the one query path
that was ported without a filter. RLS's value is not that developers
are careless; it is that the database keeps the promise on paths
nobody has written yet.

Two defects were found and fixed during the build, both in the
measurement harness rather than the system: an error-oracle
construction whose `CASE` branch was evaluated for both outcomes
(replaced with the count-division oracle above), and a first lexical
arm built on AND-combined `plainto_tsquery`, which returns nothing
for natural queries; the arm now uses the standard OR-combined
stemmed tsquery in both the service and the evaluator.

## 4. Ranking quality

96 labeled queries (per tenant: 4 topics × 3 keyword + 3 paraphrase),
grades 2 = same topic, 1 = sibling topic in the same cluster.
nDCG@10; vector arm measured with an exact scan (index scans disabled
for the eval session) so the numbers are ranking quality, not ANN
recall. Cross-tenant hits across all arms and queries: **0**.

| arm | global | acme | benco | ledgerly | verdant |
|---|---|---|---|---|---|
| lexical | 0.9972 | 1.0000 | 0.9932 | 1.0000 | 0.9958 |
| vector | 0.9925 | 0.9845 | 1.0000 | 0.9856 | 1.0000 |
| fused (RRF) | **0.9996** | 1.0000 | 1.0000 | 0.9982 | 1.0000 |

By style — keyword: lexical 1.0000 / vector 0.9850 / fused 0.9991.
Paraphrase: lexical 0.9945 / vector 1.0000 / fused 1.0000.

Reading, without inflation:

- The **best single arm flips by tenant** (lexical wins two tenants,
  vector wins two) and by query style. A global-only report would
  have crowned lexical (0.9972) and hidden that it is the *worst* arm
  for paraphrase queries and for benco-auto.
- Fusion is **never the worst arm for any tenant** and holds the
  best worst-tenant score (0.9982 vs 0.9932 lexical / 0.9845
  vector). Its one imperfection: for ledgerly-finance it lands at
  0.9982, a hair below that tenant's lexical arm alone (1.0000) —
  RRF dilutes a perfect single arm when the other arm disagrees.
  Reported, not rounded away.
- Absolute values are near ceiling by construction (see limits);
  the comparisons are the result.

## 5. Limits

- Synthetic corpus; template queries retain topic names, inflating
  all arms. Conclusions are comparative, not absolute.
- One node, 1,204 docs/database; no concurrency or load claims.
- ANN recall is excluded from the ranking eval by design (exact
  scan); the HNSW path is exercised only by the leakage suite.
- Timing channel quantified at 300 reps, not exhaustively closed.
- `ts_rank` is not BM25 (no IDF); it is Postgres's stock lexical
  ranking and is named as such. True BM25 would need pg_search or
  equivalent, which was out of scope for a from-source $0 build.
