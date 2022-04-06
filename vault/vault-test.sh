#!/usr/bin/env bash

if [[ -z "$OKTA_EMAIL_ADDR" ]]; then
  echo "Define OKTA_EMAIL_ADDR first"
  exit 1
fi

# See https://www.vaultproject.io/docs/commands#environment-variables
unset VAULT_TOKEN
export VAULT_ADDR=https://vault.test.figure.com:8200
export VAULT_TOKEN=$(vault login -method=okta username=$OKTA_EMAIL_ADDR -format=json | jq -r '.auth.client_token')

vault_ls() {
  vault kv list /vc_secrets/$1
}

alias vault-ls=vault_ls

