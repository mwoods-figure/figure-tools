#!/usr/bin/env python

import argparse
import functools
from contextlib import contextmanager
from collections.abc import Iterable
import itertools
import fnmatch
import re
import json
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

    def __post_init__(self):
        values = columns(self.line)
        self.attributes = dict(zip(self.headers, values))

    def __getattr__(self, name):
        return self.attributes[name]

    def __repr__(self) -> str:
        return f"{self.namespace}:{self.name}"

    def __str__(self) -> str:
        return self.line


def display(*args, index=None, json_output=False):
    def is_primitive(thing):
        return isinstance(thing, (type(None), bool, float, int, str))

    if json_output:
        todo()
    else:
        if len(args) == 1:
            print(args[0] if is_primitive(args[0]) else repr(args[0]))
        else:
            for i, kobj in enumerate(args, start=1):
                print(f"[{i:>2}]", kobj if is_primitive(kobj) else repr(kobj))


def command(*args, capture: bool = False) -> CompletedProcess:
    return run(args, check=True, capture_output=capture)


def lines(buffer: bytes) -> Iterator[str]:
    for line in buffer.decode("utf-8").split("\n"):
        yield line.strip()


def tokenize(command: str) -> Iterator[str]:
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
        return next(iter(thing))
    return thing


def take(lines: Iterator[str], n: int) -> Optional[str]:
    for i, line in enumerate(lines, start=1):
        if i == n:
            return line
    return None


def singleton_only(thing):
    if callable(thing):
        @functools.wraps(thing)
        def f(x):
            if iterable(x):
                raise SingleValueException
            return thing(x)
        return f
    elif iterable(thing):
        it = iter(thing)
        first = next(it, None)
        second = next(it, None)
        if second is None:
            return first
        else:
            raise SingleValueException
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
    pattern: str, objects: Iterator[KObject], transform=lambda o: o.line
) -> Iterator[KObject]:
    regex = re.compile(fnmatch.translate(pattern), re.I)
    return (o for o in objects if re.search(regex, transform(o)))


def grep_pods(pattern_or_kobj: Union[str, KObject]) -> Iterator[KObject]:
    if isinstance(pattern_or_kobj, KObject):
        require(pattern_or_kobj.is_deployment, "deployment object expected")
        return grep_objects("*", get_pods(pattern_or_kobj.selector))
    return grep_objects(pattern_or_kobj, get_pods())


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


def main_container(kobj: KObject):
    skip = {"linkerd", "linkerd-proxy"}
    return head([c for c in containers(kobj) if c not in skip])


def copy(kobj: KObject, src: str, dest: str):
    main_c = main_container(kobj)
    with_container = ["-c", main_c] if len(main_c) > 0 else []

    if kobj.is_pod:
        pod = kobj.name
    else:
        # assumed to be a deployment:
        pod = singleton_only(get_pods(kobj.selector)).name

    with kubectl(
        "cp",
        src,
        f"{kobj.namespace}/{pod}:{dest}",
        *with_container,
        capture=False,
    ) as proc:
        return proc


def exec(
    kobj: KObject, command, stdin: bool = True, tty: bool = True, host_env: bool = False
):
    main_c = main_container(kobj)
    with_container = ["-c", main_c] if len(main_c) > 0 else []

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
            NV("exec", "Run something", [("command", dict(help="Command to run"))]),
            NV("env", "Print envvars"),
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
        "pods": lambda last: grep_pods(last if last is not None else args.pattern),
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
            if thing_to_do not in actions:
                abort(f"Invalid action: {thing_to_do}")
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
                display(*output)
            else:
                display(output)
    else:
        code = 1

    sys.exit(code)
