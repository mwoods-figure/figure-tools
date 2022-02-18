# figure-tools
Figure command line tools to ease headaches 💊  

## kubeglue

Glue together pieces to `kubectl` to do things, like connect to Postgres in a single shot.

### Install

1. Download `kubeglue.py` somewhere
2. Make it executeable with `chmod +x kubeglue.py` 
3. Create a symlink in`$PATH` to `kubeglue.py`, renaming the link `kubectl-glue`
    ```bash
    $ ln -s /usr/local/bin/kubectl-glue /some/directory/lubeglue.py
    ```
4. Access `kubeglue.py` as the `glue` subcommand in `kubectl`:
    ```bash
    $ kubectl glue --help
    ```

### Example Usage

```bash

$ kubectl glue 

> # with no arguments `kubeglue.py` displays the help menu.

# Show all deplyments across all namespaces with a name matching the pattern: 
$ kubectl glue deployments "*process*bank*db*"

>
[ 1] figurepay:processor-aml-bank-db-deployment
[ 2] figurepay:processor-dorf-bankcredit-db-deployment
[ 3] figurepay:processor-email-bank-db-deployment
[ 4] figurepay:processor-fraud-bank-db-deployment
[ 5] figurepay:processor-fraud-bankcredit-db-deployment
[ 6] figurepay:processor-fund-bankcredit-db-deployment
[ 7] figurepay:processor-id-verification-bank-db-deployment
[ 8] figurepay:processor-kyc-bank-db-deployment
[ 9] figurepay:processor-telemetry-bank-db-deployment
[10] figurepay:processor-transfer-bank-db-deployment
[11] figurepay:processor-uw-bankcredit-db-deployment

# Take the 7th result:
$ kubectl glue deployments "*process*bank*db*" -7

> figurepay:processor-id-verification-bank-db-deployment

# Connect to Postgres on the 7th result:
$ kubectl glue deployments "*process*bank*db*" -7 psql

> psql (13.4 (Debian 13.4-1.pgdg100+1))
Type "help" for help.

processor-id-verification-bank=#

# Execute a SQL script through psql:
$ cat myscript.sql | kubectl glue --notty deployments "*process*bank*db*" -7 psql

> ...

# Show environment variables:
$ kubectl glue deployments "*process*bank*db*" -7 env

> ...

# Start a shell:
$ kubectl glue deployments "*process*bank*db*" -7 shell

> $

# Execute a command a shell, suppressing extra output with -q/--quiet:
$ cat README.md | kubectl glue --quiet --notty deployments "*process*bank*db*" -7 exec "wc -l"

> 68
```
