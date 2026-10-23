# Launch note — vaultsearch (Rule 10 gate, 2026-10-23)
- Novelty/kill-gate: PASS — KILLGATE.md documents prior art (RLS+pgvector pattern posts, ParadeDB, single-tenant benchmarks); claim narrowed to the packaged adversarial leakage suite + per-tenant nDCG artifact.
- Measured claims only: PASS — 16 scripted attacks against both isolation configurations on the identical schema, full attempt log in results.json; nDCG conclusions stated comparatively with corpus-ceiling caveat.
- Tests: PASS — run_tests.sh ALL TESTS PASSED (3 Go unit tests + 32 adversarial checks + eval invariants); environment verified live (PostgreSQL 16.4 + pgvector 0.8.0 from source).
- Honest wrinkles included: PASS — fusion below lexical for one tenant-topic cell; timing-probe gap reported raw (1.43x vs 1.5x threshold); two harness defects found and fixed mid-build, documented.
- Cost: PASS — $0, source-built Postgres, local.
- Secrets/PII: PASS — DB credentials are obvious runtime fixtures (fixture_app_password); synthetic corpus only.
- Identity: PASS — author habib-analyst.
- Remaining checklist items: N/A — no payments, forms, or external sends beyond the launch post (standing repo-post authorization).
LAUNCH GATE PASSED 2026-10-23.
