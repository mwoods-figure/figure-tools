# kubeglue

Glue together pieces of `kubectl` to do useful stuff.

## Install

1. Download `kubeglue.py`
2. Make `kubeglue.py` executeable with `chmod +x kubeglue.py` 
3. Create a symlink in`$PATH` to `kubeglue.py`, naming the symlink `kubectl-glue`:
    ```bash
    $ ln -s /usr/local/bin/kubectl-glue /some/directory/kubeglue.py
    ```
4. Verify the plugin is installed
    ```bash
    $  kubectl plugin list

    The following compatible plugins are available:

    /Users/mwoods-figure/bin/kubectl-glue
    ```
5. Access `kubeglue.py` as the `glue` subcommand in `kubectl`:
    ```bash
    $ kubectl glue --help
    ```

## Example Usage

```bash

# With no arguments `kubeglue.py` displays the help menu:

$ kubectl glue 
> 
usage: kubectl-glue [-h] [--json] [--nostdin] [--notty] [-q] [-v] {pods,deployments} ...

positional arguments:
  {pods,deployments}  noun
    pods              Noun: Pods
    deployments       Noun: Deployments

optional arguments:
  -h, --help          show this help message and exit
  --json
  --nostdin           Disable stdin redirection
  --notty             Disable TTY
  -q, --quiet         Suppress extra output
  -v, --verbose

# Get help on a particular subcommand:

$ kubectl glue deployments --help
>
usage: kubectl-glue deployments [-h]
                                glob-pattern
                                {containers,describe,cp,pods,exec,env,port-forward,shell,psql,pg-dump-schema} ...

positional arguments:
  glob-pattern          Glob search pattern, e.g 'foo*, processor*db*deploy*'
  {containers,describe,cp,pods,exec,env,port-forward,shell,psql,pg-dump-schema}
                        verb
    containers          Verb: Show containers
    describe            Verb: Describe object
    cp                  Verb: Copy files
    pods                Verb: List pods
    exec                Verb: Run something
    env                 Verb: Print envvars
    port-forward        Verb: Port forward
    shell               Verb: Spawn a shell
    psql                Verb: Open psql
    pg-dump-schema      Verb: Dump the schema of a Postgres database

optional arguments:
  -h, --help            show this help message and exit

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
#
# Here, we can build a simple pipeline of actions by specifying an index
# argument. An index is just an argument: -1, -42, -99, etc.
# (In general, indices 1 through 99 are supported)

$ kubectl glue pods "processor-uw-bankcredit" -3 containers
>
processor-uw-bankcredit

# We can build a simple pipeline of actions. Here, we take the 7th result. 
# The default action is to print out the thing that was indexed.

$ kubectl glue deployments "process*bank*db" -7
>
figurepay:processor-id-verification-bank-db-deployment

# Next, we can apply an action to the selected index:
# Connect to Postgres with psql on the 7th result:

$ kubectl glue deployments "process*bank*db" -7 psql
> 
psql (13.4 (Debian 13.4-1.pgdg100+1))
Type "help" for help.

processor-id-verification-bank=#

# Note, if the pattern is specific enough such that only one result is found,
# an index like -1 is not necessary:

$ kubectl glue pods "processor-uw-bankcredit-db-deployment*"
> 
figurepay:processor-uw-bankcredit-db-deployment-7d459cb5d-s7dkz

# Describing the pod works, since one result is returned:

$ kubectl glue pods "processor-uw-bankcredit-db-deployment" describe
>
[ 1] namespace: figurepay
[ 2] name: processor-uw-bankcredit-db-deployment-7d459cb5d-s7dkz
[ 3] ready: 2/2
[ 4] status: Running
[ 5] restarts: 0
[ 6] age: 62d

### More examples ###

# Execute a SQL script through psql:

$ cat myscript.sql | kubectl glue --notty deployments "process*bank*db" -7 psql
> 
...

# Show environment variables on the pod at index 7 of the listing:

$ kubectl glue deployments "process*bank*db" -7 env
> 
GOSU_VERSION=1.12
LANG=en_US.utf8
PG_MAJOR=13
PG_VERSION=13.4-1.pgdg100+1
...

# Start a shell on the pod at index 7 of the listing:

$ kubectl glue deployments "process*bank*db" -7 shell
> 
$

# Execute a command, suppressing extra output with -q/--quiet. We can pipe input
# from the local machine to the pod by making the call non interactive with --notty:

$ cat README.md | kubectl glue --quiet --notty deployments "process*bank*db" -7 exec "wc -l"
> 
68

# Forward port 5432 on the pod at index 7 in the listing, to local port 13337:

$ kubectl glue deployments "process*bank*db" -7 port-forward 13337:5432 

# Forward port 5432 on the pod at index 7 in the listing, to local port 5432:

$ kubectl glue deployments "process*bank*db" -7 port-forward 5432
```

## TODO

* Periodically cache results for calls to `kube glue deployments|pods <pattern>`
