#!/usr/bin/env python3
"""Adversarial leakage probes — HTTP API surface.

Runs against BOTH running services:
  rls deployment   on 127.0.0.1:7443 (VAULT_MODE=rls)
  naive deployment on 127.0.0.1:7444 (VAULT_MODE=naive)

The attacker holds tenant A's API key and tries every API-shaped trick:
cross-tenant search terms, direct object references, the similar-docs
path, tenant-parameter/header injection, malformed inputs (error-based
probing), and a canary hunt across many searches. A probe LEAKS if any
response contains a document belonging to tenant B, B's canary token, or
raw database error text.

Prereq ground truth (a B doc id, B canary token) is read as superuser.
Output: attacks/api_results.json + printed matrix.
"""
import json
import os
import urllib.request
import urllib.error

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = {
    "acme-cooking": "fixture-key-acme-cooking",
    "benco-auto": "fixture-key-benco-auto",
    "verdant-garden": "fixture-key-verdant-garden",
    "ledgerly-finance": "fixture-key-ledgerly-finance",
}
A, B = "acme-cooking", "benco-auto"
CANARY_B = f"CANARY-{B.upper()}-7F3A"
DEPLOYMENTS = {"rls": "http://127.0.0.1:7443", "naive": "http://127.0.0.1:7444"}
DB_ERROR_MARKERS = ("pq:", "pg_", "SQLSTATE", "tsquery", "invalid input syntax for type")


def truth():
    with psycopg.connect(host="127.0.0.1", port=7432, user="postgres",
                         dbname="vaultsearch", autocommit=True) as c:
        b_doc = c.execute("SELECT doc_id FROM documents WHERE tenant_id=%s AND topic='brake systems' LIMIT 1",
                          (B,)).fetchone()[0]
        a_doc = c.execute("SELECT doc_id FROM documents WHERE tenant_id=%s AND topic <> 'canary' LIMIT 1",
                          (A,)).fetchone()[0]
    return a_doc, b_doc


def call(base, method, path, key=None, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method)
    if key:
        req.add_header("X-Api-Key", key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, str(e)


def foreign_docs(text, own=A):
    """Parse a JSON response; return ids of results not owned by `own`."""
    try:
        payload = json.loads(text)
    except Exception:
        return []
    results = payload.get("results")
    if results is None and "doc_id" in payload:
        results = [payload]
    if not results:
        return []
    return [r["doc_id"] for r in results if r.get("tenant_id") not in (None, own)]


def contains_leak(text, own=A):
    if CANARY_B in text:
        return True, "canary token in response"
    foreign = foreign_docs(text, own)
    if foreign:
        return True, f"foreign doc ids: {foreign[:3]}"
    if any(m in text for m in DB_ERROR_MARKERS):
        return True, "raw database error text in response"
    return False, ""


def run(base_name, base, a_doc, b_doc):
    key_a = KEYS[A]
    out = []

    def rec(pid, name, leaked, evidence, status=None):
        out.append(dict(id=pid, name=name, surface="api", config=base_name,
                        expected="blocked", leaked=bool(leaked),
                        evidence=evidence, http_status=status))
        print(f"  [{base_name}] {pid} {name}: {'LEAK' if leaked else 'blocked'} — {evidence}")

    # P1 — search with B-domain terms, all three modes
    leaks = []
    for mode in ("fused", "lexical", "vector"):
        st, txt = call(base, "POST", "/search", key_a,
                       {"query": "brake pads caliper rotor brake fluid", "mode": mode, "k": 10})
        hit, why = contains_leak(txt)
        if hit:
            leaks.append(f"{mode}: {why}")
    rec("P1", "search B's domain terms (3 modes)", leaks, "; ".join(leaks) or "no foreign docs in any mode")

    # P2 — direct object reference to B's document
    st, txt = call(base, "GET", f"/documents/{b_doc}", key_a)
    hit, why = contains_leak(txt)
    rec("P2", "GET /documents/{B doc id}", hit or st == 200, why or f"status {st}", st)

    # P3 — similar-docs seeded with B's document
    st, txt = call(base, "GET", f"/similar/{b_doc}?k=10", key_a)
    hit, why = contains_leak(txt)
    rec("P3", "GET /similar/{B doc id}", hit, why or f"status {st}", st)

    # P4 — similar-docs seeded with A's own document: neighbours must stay in A
    st, txt = call(base, "GET", f"/similar/{a_doc}?k=10", key_a)
    hit, why = contains_leak(txt)
    rec("P4", "GET /similar/{A doc id} neighbour scope", hit, why or f"status {st}, all neighbours own-tenant", st)

    # P5 — tenant injection: body field + header must be ignored
    st, txt = call(base, "POST", "/search", key_a,
                   {"query": "brake pads", "mode": "fused", "k": 10, "tenant_id": B},
                   headers={"X-Tenant-Id": B})
    hit, why = contains_leak(txt)
    rec("P5", "tenant_id body field + X-Tenant-Id header injection", hit, why or "injection ignored")

    # P6 — malformed / hostile inputs (error-based probing)
    problems = []
    for body, label in (
        ({"query": "x", "k": "1; DROP TABLE documents"}, "string k"),
        ({"query": "' OR 1=1 --", "k": 10}, "SQL syntax in query"),
        ({"query": "brake", "mode": "everything", "k": 10}, "invalid mode"),
        ({"query": "brake", "k": 99999}, "oversized k"),
        ({"query": "", "k": 10}, "empty query"),
    ):
        st, txt = call(base, "POST", "/search", key_a, body)
        hit, why = contains_leak(txt)
        if hit:
            problems.append(f"{label}: {why}")
        if st == 200 and label in ("invalid mode", "empty query"):
            problems.append(f"{label}: accepted with 200")
    rec("P6", "malformed inputs / error probing (5 cases)", problems, "; ".join(problems) or "all rejected or safely empty, no DB error text")

    # P7 — canary hunt: many searches as A, incl. the literal word CANARY
    hunt_queries = ["CANARY", "canary record", "internal canary token",
                    "brake", "brake pads", "engine", "rotor", "caliper",
                    "sourdough", "budget", "soil", "pension"]
    found = []
    for q in hunt_queries:
        st, txt = call(base, "POST", "/search", key_a, {"query": q, "mode": "fused", "k": 50})
        if CANARY_B in txt:
            found.append(q)
    rec("P7", f"canary hunt across {len(hunt_queries)} searches", found,
        f"canary surfaced for queries: {found}" if found else "canary never surfaced")

    # P8 — no key / wrong key must not return data
    st1, _ = call(base, "POST", "/search", None, {"query": "brake", "k": 10})
    st2, _ = call(base, "POST", "/search", "fixture-key-WRONG", {"query": "brake", "k": 10})
    rec("P8", "missing / wrong API key", not (st1 == 401 and st2 == 401),
        f"statuses: no-key={st1}, wrong-key={st2}")
    return out


def main():
    a_doc, b_doc = truth()
    results = []
    for name, base in DEPLOYMENTS.items():
        print(f"== deployment: {name} ({base})")
        results += run(name, base, a_doc, b_doc)
    json.dump(results, open(os.path.join(HERE, "api_results.json"), "w"), indent=2)
    leaks = [r for r in results if r["leaked"]]
    print(f"\ntotal API probes: {len(results)}, leaks: {len(leaks)}")
    for r in leaks:
        print(f"  LEAK {r['config']} {r['id']} {r['name']} — {r['evidence']}")


if __name__ == "__main__":
    main()
