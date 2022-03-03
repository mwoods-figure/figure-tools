#!/usr/bin/env python

import argparse
import functools
from contextlib import contextmanager
from collections.abc import Iterable
from itertools import tee
import fnmatch
import re
import json as pyjson
import sys
import shlex
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple, Union
from subprocess import run, CompletedProcess


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
        super().__init__("expected single value but got multiple values")


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


def tokenize(command: Union[str, Iterator[str]]) -> Iterator[str]:
    if iterable(command):
        return command
    return shlex.split(command)


@contextmanager
def kubectl(*args, **kwargs):
    cp = command("kubectl", *args, **kwargs)
    capture = kwargs.get("capture")
    if capture:
        if cp.stdout is not None:
            yield lines(cp.stdout)
        else:
            yield None
    else:
        yield cp


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
        first = next(it, None)
        second = next(it, None)
        return first is None or second is not None

    if callable(thing):
        @functools.wraps(thing)
        def f(x):
            if iterable(x):
                xs = list(x)
                if has_second(xs):
                    raise SingleValueException
                return thing(head(xs))
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


def parse_portspec(portspec: str) -> Tuple[Optional[int], int]:
    """
    "8888:9999" :: [host-port:8888, pod-port:9999]
    ":9999"     :: [*random*, pod-port:9999] (k8s chooses a random host port)
    "9999"      :: [host-port:9999, pod-port:9999]
    """
    ps = portspec.strip()
    if ps.startswith(":"):
        return (None, int(ps.lstrip(":")))
    else:
        if ":" in ps:
            return tuple(map(int, ps.split(":")))
        else:
            return (int(ps), int(ps))


def port_forward(kobj: KObject, host_port: Optional[int], pod_port: int) -> CompletedProcess:
    specifier = f"{kobj.type_}/{kobj.name}"
    print(specifier)
    with_host_port = "" if host_port is None else host_port
    with_pod_port = pod_port
    with kubectl(
        "port-forward",
        "-n",
        kobj.namespace,
        specifier,
        f"{with_host_port}:{with_pod_port}",
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
            f"{kobj.type_}/{kobj.name}",
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

    with kubectl(
        "exec",
        *with_stdin,
        *with_tty,
        "-n",
        kobj.namespace,
        f"{kobj.type_}/{kobj.name}",
        *with_container,
        "--",
        *with_command,
    ) as proc:
        return proc


if __name__ == "__main__":

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
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        dest="quiet",
        help="Suppress extra output",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, dest="verbose"
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
            NV(
                "cp",
                "Copy files",
                [
                    ("src", dict(help="Copy source")),
                    ("dest", dict(help="Copy destination")),
                ],
            ),
            NV("pods", "List pods"),
            NV("exec", "Run something", [("command", dict(nargs=argparse.REMAINDER, help="Command to run"))]),
            NV("env", "Print envvars"),
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
            NV("psql", "Open psql"),
            NV("pg-dump-schema", "Dump the schema of the Postgres database"),
        ]
        for v in verbs:
            pp = p.add_parser(v.name, help=f"Verb: {v.description}")
            for name, kws in v.arguments:
                pp.add_argument(name, **kws)
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

    args = parser.parse_args()

    do_next = []
    if (noun := getattr(args, "noun", None)) is not None:
        do_next.append(noun)
    if (verb := getattr(args, "verb", None)) is not None:
        do_next.append(verb)

    if len(do_next) == 0:
        parser.print_help()
        sys.exit(0)

    if args.verbose:
        print(args)

    actions = {
        "pods": lambda last: grep_pods(args.pattern, last),
        "deployments": lambda _: grep_deployments(args.pattern),
        "containers": singleton_only(lambda last: containers(last)),
        "cp": singleton_only(lambda last: copy(last, args.src, args.dest)),
        "exec": singleton_only(
            lambda last: exec(
                last, tokenize(args.command), stdin=args.stdin, tty=args.tty
            )
        ),
        "env": singleton_only(
            lambda last: exec(last, tokenize("env"), stdin=args.stdin, tty=args.tty)
        ),
        "port-forward": singleton_only(
            lambda last: port_forward(last, *parse_portspec(args.portspec))
        ),
        "shell": singleton_only(
            lambda last: exec(last, tokenize("/bin/sh"), stdin=args.stdin, tty=args.tty)
        ),
        "psql": singleton_only(
            lambda last: exec(
                last,
                lambda env: ["psql"]
                + (["--quiet", "-t"] if args.quiet else [])
                + ["-U", find_postgres_user(env)],
                stdin=args.stdin,
                tty=args.tty,
                host_env=True,
            )
        ),
        "pg-dump-schema": singleton_only(
            lambda last: exec(
                last,
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
        if not args.quiet:
            if iterable(output):
                display(*output, json=args.json)
            else:
                display(output, json=args.json)
    else:
        code = 1

    sys.exit(code)
