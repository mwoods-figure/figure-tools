# Vault tools

## Usage

The `vault-*.sh` scripts will set the `VAULT_TOKEN` and `VAULT_ADDR` 
environment variables so subsequent calls to `vault` will function as expected.
Additionally, helper functions will be defined.

To use, add `vault-test.sh` and `vault-prod.sh` to `PATH`, then `source` as needed:

```bash
# Log into vault test:
$ source vault-test.sh

# Log into vault prod:
$ source vault-prod.sh
```

## Test it out

```bash
# List keys:

$ vault kv list /vc_secrets
> Keys
----
figure-pay/
figure-tech/
figure/
shared-services/
shared/

# As an equivalent shorthand, vault-ls is provided which uses `/vc_secrets` as 
# the root by default:

$ vault-ls
> Keys
----
figure-pay/
figure-tech/
figure/
shared-services/
shared/
```

