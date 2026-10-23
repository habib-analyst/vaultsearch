#!/bin/bash
# Start the embedding sidecar (if needed) and both vaultsearch deployments:
#   rls   -> 127.0.0.1:7443 against database vaultsearch
#   naive -> 127.0.0.1:7444 against database vaultsearch_naive
cd "$(dirname "$0")"
KEYS='{"fixture-key-acme-cooking":"acme-cooking","fixture-key-benco-auto":"benco-auto","fixture-key-verdant-garden":"verdant-garden","fixture-key-ledgerly-finance":"ledgerly-finance"}'

curl -s -m 3 http://127.0.0.1:7433/health >/dev/null || \
  (nohup python3 embed/embed_sidecar.py > logs/sidecar.log 2>&1 &)

pkill -f "[b]in/vaultsearch" 2>/dev/null; sleep 1
(VAULT_MODE=rls   VAULT_ADDR=127.0.0.1:7443 VAULT_DSN="postgres://vault_app:fixture_app_password@127.0.0.1:7432/vaultsearch"       VAULT_API_KEYS="$KEYS" nohup ./bin/vaultsearch > logs/service_rls.log 2>&1 &)
(VAULT_MODE=naive VAULT_ADDR=127.0.0.1:7444 VAULT_DSN="postgres://vault_app:fixture_app_password@127.0.0.1:7432/vaultsearch_naive" VAULT_API_KEYS="$KEYS" nohup ./bin/vaultsearch > logs/service_naive.log 2>&1 &)
sleep 3
curl -s http://127.0.0.1:7443/health; echo
curl -s http://127.0.0.1:7444/health; echo
