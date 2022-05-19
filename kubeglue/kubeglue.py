#!/usr/bin/env python

import asyncio
import argparse
import functools
from contextlib import contextmanager
from collections.abc import Iterable
from itertools import chain, tee
import fnmatch
import re
import json as pyjson
import os
import sys
import shlex
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple, Union
from subprocess import run, CompletedProcess
from functools import partial
import logging


log = logging.getLogger("glue")
DEBUG = os.environ.get("GLUEDEBUG") == "1"

POSTGRES_PORT = 5432
PORT_FORWARD_SCRIPT = """
#!/usr/bin/env bash

# Adapted from https://github.com/kubernetes/kubernetes/issues/72597#issuecomment-693149447

set -e

function cleanup {{
  echo "Cleaning up {temp_pod_name}"
  kubectl {namespace_arg} delete pod/{temp_pod_name} --grace-period 1 --wait=false
}}

trap cleanup EXIT

kubectl run {namespace_arg} --restart=Never --image=alpine/socat {temp_pod_name} -- -d -d tcp-listen:{remote_port},fork,reuseaddr tcp-connect:{remote_host}:{remote_port}
kubectl wait {namespace_arg} --for=condition=Ready pod/{temp_pod_name}
kubectl port-forward {namespace_arg} pod/{temp_pod_name} {local_port}:{remote_port}
"""


def todo():
    raise NotImplementedError


def abort(message: str):
    print(f"error: {message}")
    sys.exit(1)


def require(predicate: bool, message: str):
    if not predicate:
        abort(message)


class SingleValueException(Exception):
    def __init__(self):
        super().__init__(
            "expected single value but got multiple values [hint: use an index arg like -1, -2, -3, etc.]"
        )


@dataclass
class PortForward:
    host_port: Optional[int]
    pod_port: int
    remote_ip: Optional[str]

    @property
    def has_remote_ip(self) -> bool:
        return self.remote_ip is not None

    def to_specifier(self) -> str:
        return f"{self.host_port or ''}:{self.pod_port}"

    @classmethod
    def parse(cls, portspec: str) -> "PortForward":
        """
        Specifiers:
          "8888:9999"           :: PortForward(host_port=8888, pod_port=9999, remote_ip=None)
          ":192.0.0.1:9999"     :: PortForward(host_port=*random*, pod_port=9999, remote_ip="192.0.0.1")
          "8888:192.0.0.1:9999" :: PortForward(host_port=8888, pod_port=9999, remote_ip="192.0.0.1")
          ":9999"               :: PortForward(host_port=*random*, pod_port:9999, remote_ip=None)
          "9999"                :: PortForward(host_port=9999, pod_port=9999, remote_ip=None)
        """
        ps = portspec.strip()
        colons = ps.count(":")
        if colons == 2:
            (host, remote, pod) = ps.split(":")
            return PortForward(
                host_port=None if host.strip() == "" else int(host),
                pod_port=int(pod),
                remote_ip=remote,
            )
        elif colons == 1:
            (host, pod) = ps.split(":")
            return PortForward(
                host_port=None if host.strip() == "" else int(host),
                pod_port=int(pod),
                remote_ip=None,
            )
        elif colons == 0:
            port = int(ps)
            return PortForward(host_port=port, pod_port=port, remote_ip=None)
        raise ValueError(f"Malform port spec: {portspec}")


@dataclass
class NV:
    name: str
    description: str
    arguments: List[Tuple[str, Dict[str, str]]] = field(default_factory=list)


@dataclass(repr=False)
class KObject:
    line: str
    type_: str
    headers: List[str]
    attributes: Dict[str, str] = field(init=False)

    @property
    def is_pod(self) -> bool:
        return self.type_ == "pod"

    @property
    def is_deployment(self) -> bool:
        return self.type_ == "deployment"

    def to_specifier(self) -> str:
        return f"{self.type_}/{self.name}"

    def to_json(self) -> str:
        return pyjson.dumps(self.attributes)

    def __post_init__(self):
        values = columns(self.line)
        self.attributes = dict(zip(self.headers, values))

    def __getattr__(self, name):
        return self.attributes[name]

    def __repr__(self) -> str:
        return f"{self.namespace}:{self.name}"

    def __str__(self) -> str:
        return self.line


def inspect(*args):
    if DEBUG:
        for thing in args:
            print(f"inspect~{type(thing).__name__}::{thing}".replace("\n", "\\n"))
    return args[-1]


def display(*args, index=None, json=False):
    def is_primitive(thing) -> bool:
        return isinstance(thing, (type(None), bool, float, int, str))

    def prepare(thing, json: bool = False) -> str:
        if json:
            return pyjson.dumps(thing) if is_primitive(thing) else thing.to_json()
        else:
            return thing if is_primitive(thing) else repr(thing)

    if len(args) == 1:
        print(prepare(args[0], json=json))
    else:
        for i, kobj in enumerate(args, start=1):
            print(f"[{i:>2}] {prepare(kobj, json=json)}")


def command(*args, capture: bool = False) -> CompletedProcess:
    return run(args, check=True, capture_output=capture)


def lines(buffer: bytes) -> Iterator[str]:
    for line in buffer.decode("utf-8").split("\n"):
        yield line.strip()


def flatten(list_of_lists):
    return chain.from_iterable(list_of_lists)


def tokenize(command: Union[str, Iterator[str]]) -> Iterator[str]:
    return list(
        flatten(
            shlex.split(token)
            for token in (command if iterable(command) else shlex.split(command))
        )
    )


@contextmanager
def kubectl(*args, **kwargs):
    proc = command("kubectl", *args, **kwargs)
    log.info(f"RUN >> kubectl {' '.join(args)} * {kwargs}")
    capture = kwargs.get("capture")
    if capture:
        if proc.stdout is not None:
            yield lines(proc.stdout)
        else:
            yield None
    else:
        yield proc


def iterable(thing) -> bool:
    if isinstance(thing, str):
        return False
    if isinstance(thing, Iterable):
        return True
    try:
        iter(thing)
        return True
    except:
        return False


def head(thing):
    if iterable(thing):
        return next(iter(thing), None)
    return thing


def take(lines: Iterator[str], n: int) -> Optional[str]:
    for i, line in enumerate(lines, start=1):
        if i == n:
            return line
    return None


def singleton_only(thing):
    def has_second(g):
        it = iter(g)
        first_end, second_end = object(), object()
        first = next(it, first_end)
        if first == first_end:
            return False
        return next(it, second_end) != second_end

    if callable(thing):

        @functools.wraps(thing)
        def f(x):
            if iterable(x):
                xs = list(x)
                if has_second(xs):
                    raise SingleValueException
                # If nothing left, return None to stop
                return None if len(xs) == 0 else thing(head(xs))
            else:
                return thing(x)

        return f
    elif iterable(thing):
        xs = list(thing)
        if has_second(xs):
            raise SingleValueException
        return head(xs)
    # already a scalar:
    return thing


def find_postgres_user(env: Dict[str, str]) -> str:
    candidates = ["POSTGRES_USER", "PG_USER", "DB_USER"]
    for candidate in candidates:
        if candidate in env:
            return env[candidate]
    raise Exception("env:no postgres user found")


def columns(line: str) -> List[str]:
    return line.split()


def get_pods(selector: Optional[str] = None) -> Iterator[KObject]:
    headers = ["namespace", "name", "ready", "status", "restarts", "age"]
    with_selector = [f"--selector={selector}"] if selector is not None else []
    with kubectl(
        "get", "pods", "-A", "--no-headers=true", *with_selector, capture=True
    ) as lines:
        return (
            KObject(line, "pod", headers) for line in lines if len(line.strip()) > 0
        )


def get_deployments() -> Iterator[KObject]:
    headers = [
        "namespace",
        "name",
        "ready",
        "up-to-date",
        "available",
        "age",
        "containers",
        "images",
        "selector",
    ]
    with kubectl(
        "get", "deployments", "-A", "--no-headers=true", "-o", "wide", capture=True
    ) as lines:
        return (
            KObject(line, "deployment", headers)
            for line in lines
            if len(line.strip()) > 0
        )


def grep_objects(
    pattern: str, kobjs: Iterator[KObject], transform=lambda o: o.line
) -> Iterator[KObject]:
    # it's useful to prepend+append "*" to the pattern if they're not there because
    # this usually the intention:
    p = pattern.strip()
    if not p.startswith("*"):
        p = "*" + p
    if not p.endswith("*"):
        p += "*"
    regex = re.compile(fnmatch.translate(p), re.I)
    return (o for o in kobjs if re.search(regex, transform(o)))


def grep_pods(
    pattern: Union[str, KObject], kobj: Optional[KObject] = None
) -> Iterator[KObject]:
    if kobj is not None:
        require(kobj.is_deployment, "deployment object expected")
        pods = get_pods(kobj.selector)
    else:
        pods = get_pods()
    return grep_objects(pattern, pods)


def grep_deployments(pattern: str) -> Iterator[KObject]:
    return grep_objects(pattern, get_deployments())


def describe(kobj: KObject) -> Iterator[str]:
    return (f"{key}: {value}" for key, value in kobj.attributes.items())


def containers(kobj: KObject) -> Iterator[str]:
    if kobj.is_pod:
        jsonpath = "jsonpath={.spec.containers[*].name}"
    elif kobj.is_deployment:
        jsonpath = "jsonpath={.spec.template.spec.containers[*].name}"
    else:
        todo()
    with kubectl(
        "get", "-n", kobj.namespace, kobj.type_, kobj.name, "-o", jsonpath, capture=True
    ) as lines:
        return columns(head(lines))


def image(kobj: KObject) -> str:
    if kobj.is_pod:
        raise NotImplementedError("Only deployment supported")
    elif kobj.is_deployment:
        jsonpath = "jsonpath={$.spec.template.spec.containers..image}"
    with kubectl(
        "get", "-n", kobj.namespace, kobj.type_, kobj.name, "-o", jsonpath, capture=True
    ) as lines:
        return columns(head(lines))


def primary_container(kobj: KObject) -> Optional[str]:
    skip = {"linkerd", "linkerd-proxy"}
    return head([c for c in containers(kobj) if c not in skip])


def primary_pod(kobj: KObject) -> KObject:
    if kobj.is_pod:
        return kobj
    else:
        # assumed to be a deployment:
        return singleton_only(get_pods(kobj.selector))


def copy(kobj: KObject, src: str, dest: str) -> CompletedProcess:
    container = primary_container(kobj)
    with_container = ["-c", container] if container else []

    pod = primary_pod(kobj)

    with kubectl(
        "cp",
        src,
        f"{kobj.namespace}/{pod}:{dest}",
        *with_container,
        capture=False,
    ) as proc:
        return proc


def port_forward(kobj: KObject, fwd_spec: PortForward) -> CompletedProcess:
    if fwd_spec.has_remote_ip:
        raise NotImplementedError(
            "Port forwarding with remote IP is not a pod or deployment operation.\n"
            + "Try kube glue port-forward <port-spec>"
        )
    else:
        with kubectl(
            "port-forward",
            "-n",
            kobj.namespace,
            kobj.to_specifier(),
            fwd_spec.to_specifier(),
        ) as proc:
            return proc


def exec(
    kobj: KObject, command, stdin: bool = True, tty: bool = True, host_env: bool = False
):
    container = primary_container(kobj)
    with_container = ["-c", container] if container else []

    env = {}
    if host_env:
        with kubectl(
            "exec",
            "-n",
            kobj.namespace,
            kobj.to_specifier(),
            *with_container,
            "--",
            "env",
            capture=True,
        ) as lines:
            for line in lines:
                parts = line.split("=")
                key = parts[0]
                value = parts[1] if len(parts) > 1 else ""
                env[key.strip()] = value.strip()

    with_stdin = ["--stdin"] if stdin else []
    with_tty = ["--tty"] if tty else []
    with_command = command(env) if callable(command) else command
    if with_command[0] != "--":
        with_command.insert(0, "--")

    with kubectl(
        "exec",
        *with_stdin,
        *with_tty,
        "-n",
        kobj.namespace,
        kobj.to_specifier(),
        *with_container,
        *with_command,
    ) as proc:
        return proc


def logs(kobj: KObject, rest=[]) -> CompletedProcess:

    container = primary_container(kobj)
    with_container = ["-c", container] if container else []

    with kubectl(
        "logs", "-n", kobj.namespace, kobj.to_specifier(), *with_container, *rest
    ) as proc:
        return proc


def setup_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", default=False, dest="json")
    parser.add_argument(
        "--nostdin",
        action="store_false",
        default=True,
        dest="stdin",
        help="Disable stdin redirection",
    )
    parser.add_argument(
        "--notty", action="store_false", default=True, dest="tty", help="Disable TTY"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        dest="verbose",
        help="Include more output",
    )

    # Supports -1 to -99
    def build_index_args(parser):
        for i in range(1, 100):
            parser.add_argument(
                f"-{i}",
                action="store_const",
                const=i,
                required=False,
                dest="index",
                help=argparse.SUPPRESS,
            )

    def build_verbs(parser):
        p = parser.add_subparsers(help="verb", dest="verb")
        verbs = [
            NV("containers", "Show containers"),
            NV("describe", "Describe object"),
            NV(
                "cp",
                "Copy files",
                [
                    ("src", dict(help="Copy source")),
                    ("dest", dict(help="Copy destination")),
                ],
            ),
            NV("pods", "List pods"),
            NV("image", "Show deployment image"),
            NV("exec", "Run something"),
            NV("env", "Print envvars"),
            NV("logs", "View logs"),
            NV(
                "port-forward",
                "Port forward",
                [
                    (
                        "portspec",
                        dict(help="Port specifier: either <host>:<pod> or :<pod>"),
                    )
                ],
            ),
            NV("shell", "Spawn a shell"),
            NV(
                "psql",
                "Open psql",
                [
                    (
                        "--forward",
                        dict(
                            type=int,
                            metavar="PORT",
                            help="Port-forward instead of opening a shell",
                        ),
                    )
                ],
            ),
            NV("pg-dump-schema", "Dump the schema of a Postgres database"),
        ]
        for v in verbs:
            pp = p.add_parser(v.name, help=f"Verb: {v.description}")
            for name, kws in v.arguments:
                pp.add_argument(name, **kws)
            pp.add_argument(
                "rest",
                nargs=argparse.REMAINDER,
                help="Unconsumed arguments in `rest` will be passed to kubectl",
            )

            # build_index_args(pp)

    def build_nouns(parser):
        p = parser.add_subparsers(help="noun", dest="noun")
        nouns = [
            NV(
                "pods",
                "Pods",
                [
                    (
                        "pattern",
                        dict(
                            metavar="glob-pattern",
                            help="Glob pattern, e.g. 'foo*', 'processor*db*deploy*'",
                        ),
                    )
                ],
            ),
            NV(
                "deployments",
                "Deployments",
                [
                    (
                        "pattern",
                        dict(
                            metavar="glob-pattern",
                            help="Glob search pattern, e.g 'foo*, processor*db*deploy*'",
                        ),
                    )
                ],
            ),
        ]
        for n in nouns:
            pp = p.add_parser(n.name, help=f"Noun: {n.description}")
            build_index_args(pp)
            for name, kws in n.arguments:
                pp.add_argument(name, **kws)
            build_verbs(pp)

    build_index_args(parser)
    build_nouns(parser)

    args, remaining = parser.parse_known_args()

    return args, remaining


def runner(args, remaining):
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO)

    do_next = []
    if (noun := getattr(args, "noun", None)) is not None:
        do_next.append(noun)
    if (verb := getattr(args, "verb", None)) is not None:
        do_next.append(verb)

    if len(do_next) == 0:
        parser.print_help()
        sys.exit(0)

    if args.verbose:
        log.info(args)
        log.info(remaining)

    actions = {
        "pods": lambda last: grep_pods(args.pattern, inspect(last)),
        "deployments": lambda _: grep_deployments(args.pattern),
        "containers": singleton_only(lambda last: containers(inspect(last))),
        "cp": singleton_only(lambda last: copy(inspect(last), args.src, args.dest)),
        "exec": singleton_only(
            lambda last: exec(
                inspect(last),
                tokenize(args.rest) + tokenize(remaining),
                stdin=args.stdin,
                tty=args.tty,
            )
        ),
        "describe": singleton_only(lambda last: describe(inspect(last))),
        "image": singleton_only(lambda last: image(inspect(last))),
        "env": singleton_only(
            lambda last: exec(
                inspect(last), tokenize("env"), stdin=args.stdin, tty=args.tty
            )
        ),
        "logs": singleton_only(
            lambda last: logs(inspect(last), tokenize(args.rest) + tokenize(remaining))
        ),
        "port-forward": singleton_only(
            lambda last: port_forward(inspect(last), PortForward.parse(args.portspec))
        ),
        "shell": singleton_only(
            lambda last: exec(
                inspect(last), tokenize("/bin/sh"), stdin=args.stdin, tty=args.tty
            )
        ),
        "psql": singleton_only(
            lambda last: port_forward(
                inspect(last), PortForward.parse(f"{args.forward}:{POSTGRES_PORT}")
            )
            if args.forward is not None
            else exec(
                inspect(args, last),
                lambda env: ["psql"]
                + (["--quiet", "-t"] if not args.verbose else [])
                + ["-U", find_postgres_user(env)],
                stdin=args.stdin,
                tty=args.tty,
                host_env=True,
            )
        ),
        "pg-dump-schema": singleton_only(
            lambda last: exec(
                inspect(last),
                lambda env: tokenize(f"pg_dump -s -U {find_postgres_user(env)}"),
                stdin=args.stdin,
                tty=args.tty,
                host_env=True,
            )
        ),
    }

    output, code, index = None, 0, args.index

    while len(do_next) > 0:
        if (thing_to_do := do_next.pop(0)) is None:
            break
        try:
            output = actions[thing_to_do](output)
            if output is None:
                break
        except SingleValueException as e:
            abort(e)
        except:
            raise
        if index is not None:
            if iterable(output):
                output = take(output, index)
            else:
                output = None
            index = None

    if output is not None:
        if isinstance(output, CompletedProcess):
            if args.verbose:
                log.info(output)
        else:
            if iterable(output):
                display(*output, json=args.json)
            else:
                display(output, json=args.json)
    else:
        code = 1

    sys.exit(code)


if __name__ == "__main__":
    args, remaining = setup_args()
    runner(args, remaining)

