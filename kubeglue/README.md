# kubeglue

Glue together pieces of `kubectl` to do useful stuff.

## Install

1. Download `kubeglue.py`
2. Make `kubeglue.py` executeable with `chmod +x kubeglue.py` 
3. Create a symlink in`$PATH` to `kubeglue.py`, naming the symlink `kubectl-glue`:
    ```bash
    $ ln -s /usr/local/bin/kubectl-glue /some/directory/lubeglue.py
    ```
4. Access `kubeglue.py` as the `glue` subcommand in `kubectl`:
    ```bash
    $ kubectl glue --help
    ```

## Example Usage

```bash

# With no arguments `kubeglue.py` displays the help menu.
$ kubectl glue 
> 
usage: kubectl-glue [-h] [--json] [--nostdin] [--notty] [-q] [-v] {pods,deployments} ...

# Get help on a particular subcommand:
$ kubectl glue deployments --help
>
usage: kubectl-glue deployments [-h] glob-pattern {containers,exec,env,shell,psql,pg-dump-schema} ...

positional arguments:
  glob-pattern          Glob search pattern, e.g 'foo*, processor*db*deploy*'
  {containers,exec,env,shell,psql,pg-dump-schema}
                        verb
    containers          Verb: Show containers
    exec                Verb: Run something
    env                 Verb: Print envvars
    shell               Verb: Spawn a shell
    psql                Verb: Open psql
    pg-dump-schema      Verb: Dump the schema of the Postgres database

optional arguments:
  -h, --help            show this help message and exit


positional arguments:
  {pods,deployments}  noun
    pods              Noun: Pods
    deployments       Noun: Deployment

optional arguments:
  -h, --help          show this help message and exit
  --json
  --nostdin           Redirect stdin
  --notty             Use TTY mode
  -q, --quiet         Suppress extra output
  -v, --verbose

# Show all deployments across all namespaces with a name matching the pattern: 
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

# The same works with pods as well:
$ kubectl glue pods "*process*bank*db*"
>
[ 1] figurepay:processor-aml-bank-db-deployment-6cd548b85f-xvzpc
[ 2] figurepay:processor-dorf-bankcredit-cross-cutter-deployment-64b7db7crwf5m
[ 3] figurepay:processor-dorf-bankcredit-db-deployment-8597f69fcf-8rxpz
[ 4] figurepay:processor-email-bank-db-deployment-5fd4f697-ldwh8
[ 5] figurepay:processor-fraud-bank-db-deployment-7b57d56575-jlmgg
[ 6] figurepay:processor-fraud-bankcredit-db-deployment-7bfcd6d456-crvvz
[ 7] figurepay:processor-fraud-bankcredit-pgbouncer-deployment-5d6949fdb95vrsr
[ 8] figurepay:processor-fund-bankcredit-db-deployment-5fbc7977b9-dbqkj
[ 9] figurepay:processor-id-verification-bank-db-deployment-f7fdc46b6-6fcb7
[10] figurepay:processor-kyc-bank-db-deployment-5c6b44c668-fvs94
[11] figurepay:processor-kyc-bank-deployment-64b86bdb5b-tbxzj
[12] figurepay:processor-kyc-bank-pgbouncer-deployment-85778db574-gn797
[13] figurepay:processor-telemetry-bank-db-deployment-5fdd74958-9z2zf
[14] figurepay:processor-transfer-bank-db-deployment-5688644b64-4nqjh
[15] figurepay:processor-uw-bankcredit-db-deployment-7d459cb5d-s7dkz

# Show the containers of a pod or deployment:
$ kubectl glue pods "*processor-uw-bankcredit*" -3 containers
>
processor-uw-bankcredit

# We can build a simple pipeline of actions:
# Here, we take the 7th result. An index is just an argument: -1, -42, -99, etc.
# (In general, indices 1 through 99 are supported)
$ kubectl glue deployments "*process*bank*db*" -7
>
figurepay:processor-id-verification-bank-db-deployment

# Connect to Postgres with psql on the 7th result:
$ kubectl glue deployments "*process*bank*db*" -7 psql
> 
psql (13.4 (Debian 13.4-1.pgdg100+1))
Type "help" for help.

processor-id-verification-bank=#

# Execute a SQL script through psql:
$ cat myscript.sql | kubectl glue --notty deployments "*process*bank*db*" -7 psql
> 
...

# Show environment variables:
$ kubectl glue deployments "*process*bank*db*" -7 env
> 
...

# Start a shell:
$ kubectl glue deployments "*process*bank*db*" -7 shell
> 
$

# Execute a command, suppressing extra output with -q/--quiet:
$ cat README.md | kubectl glue --quiet --notty deployments "*process*bank*db*" -7 exec "wc -l"
> 
68
```

## TODO

* Periodically cache results for calls to `kube glue deployments|pods <pattern>`
