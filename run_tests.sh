#!/bin/bash
# vaultsearch test entry point. Requires: Postgres running (tools/start_pg.sh),
# corpus loaded (loader/load.py), services running (run_services.sh).
# Asserts the security invariants; prints PASS/FAIL per stage.
set -u
cd "$(dirname "$0")"
export PATH=$HOME/workspace/tools/go/bin:$PATH GOPATH=$HOME/workspace/tools/gopath GOCACHE=$HOME/workspace/tools/gocache GOFLAGS=-mod=mod
FAIL=0

echo "== go unit tests (RRF fusion, auth)"
(cd service && go test ./...) || FAIL=1

echo "== leakage suite (SQL surface)"
python3 attacks/leakage_suite.py > logs/test_leakage.log 2>&1
python3 - <<'EOF' || FAIL=1
import json
rows = json.load(open("attacks/leakage_results.json"))
rls = [r for r in rows if r["config"] == "vaultsearch"]
assert rls and not any(r["leaked"] for r in rls), "RLS config leaked!"
print(f"PASS: RLS config {len(rls)}/{len(rls)} SQL attacks blocked")
EOF

echo "== API probes"
python3 attacks/api_probes.py > logs/test_api.log 2>&1
python3 - <<'EOF' || FAIL=1
import json
rows = json.load(open("attacks/api_results.json"))
rls = [r for r in rows if r["config"] == "rls"]
assert rls and not any(r["leaked"] for r in rls), "RLS deployment leaked!"
print(f"PASS: RLS deployment {len(rls)}/{len(rls)} API probes blocked")
EOF

echo "== ranking eval invariants"
python3 - <<'EOF' || FAIL=1
import json
s = json.load(open("eval/ndcg_results.json"))["summary"]
assert s["cross_tenant_hits_in_eval"] == 0, "cross-tenant hit in eval!"
assert s["global"]["fused"] >= s["global"]["vector"], "fusion below vector arm globally"
print(f"PASS: 0 cross-tenant hits across {s['queries']} eval queries; fused nDCG@10 = {s['global']['fused']}")
EOF

[ "$FAIL" = 0 ] && echo "ALL TESTS PASSED" || echo "TESTS FAILED"
exit $FAIL
