#!/usr/bin/env python3
"""Adversarial cross-tenant leakage suite for vaultsearch — SQL surface.

Threat model: the attacker controls SQL that runs as the application role
(vault_app) with the session tenant context set to their OWN tenant (or
unset — a code path that forgot). This is exactly the position of any
present or future query path, a SQL-injection foothold, or a compromised
service instance. The one thing out of scope is the app voluntarily
setting the context to the victim: in the RLS design the context is the
credential, set from the authenticated session only.

Every attack runs against BOTH databases:
  vaultsearch        — FORCE RLS, isolation in the database
  vaultsearch_naive  — identical schema/grants, no RLS (app-filter only)
and the suite records expected-blocked vs actually-leaked for each.

Attacker tenant A = acme-cooking. Victim tenant B = benco-auto.

Output: attacks/leakage_results.json + a printed matrix.
"""
import json
import os
import statistics
import sys
import time

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "embed"))

HOST, PORT = "127.0.0.1", 7432
APP = dict(host=HOST, port=PORT, user="vault_app", password="fixture_app_password")
SUPER = dict(host=HOST, port=PORT, user="postgres")
A, B = "acme-cooking", "benco-auto"
CANARY_B = f"CANARY-{B.upper()}-7F3A"


def app_conn(dbname):
    return psycopg.connect(dbname=dbname, autocommit=True, **APP)


def ground_truth():
    with psycopg.connect(dbname="vaultsearch", autocommit=True, **SUPER) as c:
        b_doc = c.execute(
            "SELECT doc_id, title, body FROM documents WHERE tenant_id=%s AND topic='brake systems' LIMIT 1",
            (B,)).fetchone()
        b_canary = c.execute(
            "SELECT doc_id FROM documents WHERE tenant_id=%s AND topic='canary'", (B,)).fetchone()[0]
        a_doc = c.execute(
            "SELECT doc_id FROM documents WHERE tenant_id=%s AND topic <> 'canary' LIMIT 1",
            (A,)).fetchone()[0]
    return b_doc, b_canary, a_doc


def fmt_vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def run_attacks(dbname, gt):
    (b_doc_id, b_title, b_body), b_canary, a_doc = gt
    out = []

    def rec(aid, name, leaked, evidence):
        out.append(dict(id=aid, name=name, surface="sql", config=dbname,
                        expected="blocked", leaked=bool(leaked), evidence=evidence))
        print(f"  [{dbname}] {aid} {name}: {'LEAK' if leaked else 'blocked'} — {evidence}")

    with app_conn(dbname) as c:
        # A1 — no tenant context at all (the forgotten-set_config code path)
        rows = c.execute("SELECT tenant_id, count(*) FROM documents GROUP BY 1").fetchall()
        foreign = sum(n for t, n in rows if t != A)
        rec("A1", "unscoped SELECT, no tenant context", foreign > 0,
            f"visible rows by tenant: {rows}")

        # A2 — context=A, crafted predicates fishing for B
        c.execute("SELECT set_config('app.tenant_id', %s, false)", (A,))
        leaked_rows = 0
        for probe in (
            "SELECT doc_id, tenant_id FROM documents WHERE tenant_id = %s",
            "SELECT doc_id, tenant_id FROM documents WHERE tenant_id = %s OR tenant_id = %s",
            "SELECT doc_id, tenant_id FROM documents WHERE doc_id = %s",
        ):
            params = (B,) if probe.count("%s") == 1 else ((B, A) if " OR " in probe else (b_doc_id,))
            got = c.execute(probe, params).fetchall()
            leaked_rows += sum(1 for _, t in got if t != A)
        rec("A2", "context=A + crafted tenant predicates", leaked_rows > 0,
            f"foreign rows returned across 3 probes: {leaked_rows}")

        # A3 — nearest-neighbour probe seeded with B's own document text,
        # embedded by the attacker (they possess the text). Run via the
        # shared HNSW index (default plan) and again as an exact scan.
        from embed_sidecar import embed
        b_vec = fmt_vec(embed([b_title + " " + b_body])[0])
        for suffix, setup in (("ann", None), ("exact", "SET enable_indexscan = off")):
            if setup:
                c.execute(setup)
            got = c.execute(
                "SELECT doc_id, tenant_id FROM documents ORDER BY embedding <=> %s::vector LIMIT 10",
                (b_vec,)).fetchall()
            if setup:
                c.execute("SET enable_indexscan = on")
            foreign = [d for d, t in got if t != A]
            rec(f"A3-{suffix}", f"NN probe seeded with B's text ({suffix})", len(foreign) > 0,
                f"top-10 neighbours: {len(foreign)} foreign ({foreign[:3]})")

        # A4 — error-based existence oracle on B's doc id.
        # Construction: 1/(count of rows with this id). A VISIBLE row gives
        # count=1 (no error); an invisible or nonexistent row gives count=0
        # (division by zero). The leak question is purely: does an existing
        # B doc behave DIFFERENTLY from a nonexistent id?
        def oracle(doc_id):
            try:
                c.execute(
                    "SELECT 1/(SELECT count(*) FROM documents WHERE doc_id=%s)",
                    (doc_id,))
                return False  # no error -> row visible to this role
            except psycopg.errors.DivisionByZero:
                return True   # error -> row invisible/nonexistent
        err_b, err_none = oracle(b_doc_id), oracle("zzzz-nonexistent")
        rec("A4", "error oracle: does B's doc exist?", err_b != err_none,
            f"division-error for B doc: {err_b}, for nonexistent id: {err_none}"
            f" ({'identical behaviour = no oracle' if err_b == err_none else 'behaviour differs = existence oracle'})")

        # A5 — direct privilege-escalation / policy-removal attempts
        attempts = [
            ("SET ROLE vault_owner", "SELECT 1 FROM (SELECT set_config('x','y',false)) s"),
            ("ALTER TABLE documents DISABLE ROW LEVEL SECURITY", None),
            ("DROP POLICY tenant_isolation ON documents", None),
            ("CREATE EXTENSION IF NOT EXISTS pg_stat_statements", None),
            ("COPY documents TO '/tmp/vaultsearch_copy_probe'", None),
        ]
        succeeded = []
        for sql, _ in attempts:
            try:
                c.execute(sql)
                succeeded.append(sql)
            except Exception:
                pass
        rec("A5", "privilege escalation / policy removal (5 attempts)", len(succeeded) > 0,
            f"succeeded: {succeeded if succeeded else 'none'}")

        # A6 — cross-tenant write attempts (insert into B, retitle B's doc)
        write_leaks = []
        try:
            c.execute(
                "INSERT INTO documents (doc_id, tenant_id, topic, cluster_, title, body, embedding)"
                " VALUES ('evil-0001', %s, 'x', 'x', 'injected', 'injected', %s::vector)",
                (B, fmt_vec([0.0] * 384)))
            write_leaks.append("insert-into-B")
        except Exception:
            pass
        try:
            n = c.execute("UPDATE documents SET title = title WHERE doc_id = %s", (b_doc_id,)).rowcount
            if n:
                write_leaks.append(f"update-B-rowcount-{n}")
        except Exception:
            pass
        rec("A6", "cross-tenant writes (insert / update)", len(write_leaks) > 0,
            f"succeeded: {write_leaks if write_leaks else 'none'}")

        # A7 — timing side channel: median latency of point lookups
        def median_ms(doc_id, reps=300):
            # doc_id is a suite-controlled constant; literal SQL avoids the
            # parameter-type clash of EXECUTE with bound parameters.
            stmt = f"SELECT doc_id FROM documents WHERE doc_id = '{doc_id}'"
            times = []
            for _ in range(reps):
                t0 = time.perf_counter()
                c.execute(stmt)
                times.append((time.perf_counter() - t0) * 1000)
            return statistics.median(times)
        med_b, med_none = median_ms(b_doc_id), median_ms("zzzz-nonexistent")
        ratio = med_b / med_none if med_none else float("inf")
        distinguishable = ratio > 1.5 or ratio < 0.67
        rec("A7", "timing: B-doc lookup vs nonexistent lookup", distinguishable,
            f"median B={med_b:.3f}ms nonexistent={med_none:.3f}ms ratio={ratio:.2f} (300 reps)")

    # cleanup of anything the naive config let through (as superuser)
    with psycopg.connect(dbname=dbname, autocommit=True, **SUPER) as c:
        c.execute("DELETE FROM documents WHERE doc_id = 'evil-0001'")
    return out


def main():
    gt = ground_truth()
    results = []
    for dbname in ("vaultsearch", "vaultsearch_naive"):
        print(f"== {dbname}")
        results += run_attacks(dbname, gt)
    json.dump(results, open(os.path.join(HERE, "leakage_results.json"), "w"), indent=2)
    leaks = [r for r in results if r["leaked"]]
    print(f"\ntotal attacks: {len(results)}, leaks: {len(leaks)}")
    for r in leaks:
        print(f"  LEAK {r['config']} {r['id']} {r['name']}")


if __name__ == "__main__":
    main()
