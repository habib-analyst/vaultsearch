# Kill-gate — vaultsearch

Question: does an available tool already package (1) multi-tenant hybrid
search on real Postgres (RLS + pgvector + lexical) with (2) an adversarial
cross-tenant leakage suite that publishes its attempt log, and (3) per-tenant
ranking quality (nDCG), not just a global number?

## What exists

- **The RLS + pgvector pattern is well documented as advice.** Blog posts
  (e.g. "Your WHERE clause is not a security boundary", dev.to; the Supabase
  RAG-with-permissions guide; the AWS Database Blog on multi-tenant isolation
  with RLS) show the policy shape and warn about real footguns
  (SECURITY DEFINER search functions bypassing RLS, owners bypassing
  non-FORCE policies). They are patterns, not systems, and none of them
  attacks its own setup.
- **Leak-testing advice exists, closest: a Vertex Frontier post** with
  pytest negative-test snippets (canary strings seeded in tenant B,
  unscoped-query rejection, pg_roles checks) for vector databases in
  general. It is a consultancy article with illustrative snippets — no
  Postgres hybrid-search system, no six-class attempt log, no naive
  contrast, no ranking measurement. A dev.to post ("Your multi-tenant
  SaaS needs a test that tries to leak data") makes the same point
  generically, and a Substack build log runs RLS drills on a toy CRUD app.
- **Hybrid search on Postgres is a solved demo**: ParadeDB/pg_search
  (BM25 + vectors), Vespa, Typesense, and dozens of tutorials fuse lexical
  and vector arms with RRF. None of them are multi-tenant security
  artifacts.
- **Hybrid ranking benchmarks exist but are single-tenant**: engine
  comparisons (aimultiple) and a careful measured study (devops-daily,
  FTS vs pgvector vs hybrid with bootstrap CIs) report one global nDCG
  for one corpus with no tenants at all.

## The gap (the claim)

Nobody ships the pair as one measured artifact:

1. an **adversarial leakage suite** against a real RLS deployment —
   no-context queries, crafted predicates, nearest-neighbour probes
   seeded with another tenant's text, error oracles, direct app-role
   access and privilege-escalation attempts, timing probes — with the
   full attempt log published, run against **both** the RLS schema and
   the identical schema with application-filter-only isolation, so the
   value of the database backstop is a measured number, not a slogan;
2. **per-tenant nDCG@10** for lexical / vector / fused arms, showing
   whether fusion holds up for every tenant or only on average.

## Verdict

**PROCEED.** The components are all known; the packaged, measured proof —
leakage attempts vs two isolation strategies, and ranking quality broken
down per tenant — is not available as a tool or published artifact.
Claims are narrowed accordingly: this is a measurement artifact, not a
new search algorithm and not a new security mechanism.
