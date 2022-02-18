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
from typing import Dict, Iterator, List, Optional, Tuple
from subprocess import run, CompletedProcess


def todo():
    raise NotImplementedError


class SingleValueException(Exception):
    def __init__(self):
        super().__init__("expected single value but got multiple values")


@dataclass
class NV:
    name: str
    description: str
    arguments: List[Tuple[str, Dict[str, str]]] = field(default_factory=list)


@dataclass
class KObject:
    ns: str
    ident: str
    otype: str

    @property
    def args(self):
        return [self.ns, self.otype, self.ident]

    def __str__(self) -> str:
        return f"{self.ns}:{self.ident}"


def display(*args, index=None, json_output=False):
    if json_output:
        todo()
    else:
        if len(args) == 1:
            print(args[0])
        else:
            for i, kobj in enumerate(args, start=1):
                print(f"[{i:>2}]", kobj)


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


def singleton_only(f):
    @functools.wraps(f)
    def g(thing):
        if iterable(thing):
            raise SingleValueException
        return f(thing)

    return g


def find_postgres_user(env: Dict[str, str]) -> str:
    candidates = ["POSTGRES_USER", "PG_USER", "DB_USER"]
    for candidate in candidates:
        if candidate in env:
            return env[candidate]
    raise Exception("env:no postgres user found")


def columns(line: str) -> List[str]:
    return line.split()


def get_all_pods() -> Iterator[str]:
    with kubectl("get", "pods", "-A", capture=True) as lines:
        return lines


def get_all_deployments() -> Iterator[str]:
    with kubectl("get", "deployments", "-A", capture=True) as lines:
        return lines


def filter_objects(objects, type_, f) -> Iterator[KObject]:
    for (ns, ident, *_) in (c for line in objects if len(c := columns(line)) > 0):
        if f(ns, ident):
            yield KObject(ns, ident, type_)


def grep_objects(pattern: str, type_: str, objects) -> Iterator[KObject]:
    pat_regex = fnmatch.translate(pattern)
    return filter_objects(
        objects, type_, lambda _, ident: re.search(pat_regex, ident, re.I)
    )


def grep_pods(pattern: str):
    return grep_objects(pattern, "pod", get_all_pods())


def grep_deployments(pattern: str):
    return grep_objects(pattern, "deployment", get_all_deployments())


def containers(namespace: str, type_: str, ident: str) -> Iterator[str]:
    if type_ == "pod":
        jsonpath = "jsonpath={.spec.containers[*].name}"
    elif type_ == "deployment":
        jsonpath = "jsonpath={.spec.template.spec.containers[*].name}"
    else:
        todo()
    with kubectl(
        "get", "-n", namespace, type_, ident, "-o", jsonpath, capture=True
    ) as lines:
        return columns(head(lines))


def exec(
    namespace: str,
    type_: str,
    ident: str,
    command,
    stdin: bool = True,
    tty: bool = True,
    host_env: bool = False,
):
    cs = containers(namespace, type_, ident)
    # skip anything associated with linkerd:
    main_container = head([c for c in cs if "linkerd" not in c])
    with_container = ["-c", main_container] if len(main_container) > 0 else []

    env = {}
    if host_env:
        with kubectl(
            "exec",
            "-n",
            namespace,
            f"{type_}/{ident}",
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
        namespace,
        f"{type_}/{ident}",
        *with_container,
        "--",
        *with_command,
    ) as cp:
        return cp


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", default=False, dest="json")
    parser.add_argument(
        "--nostdin",
        action="store_false",
        default=True,
        dest="stdin",
        help="Redirect stdin",
    )
    parser.add_argument(
        "--notty", action="store_false", default=True, dest="tty", help="Use TTY mode"
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
        "pods": lambda prev: grep_pods(args.pattern),
        "deployments": lambda prev: grep_deployments(args.pattern),
        "containers": singleton_only(lambda prev: containers(*prev.args)),
        "exec": singleton_only(
            lambda prev: exec(
                *prev.args, tokenize(args.command), stdin=args.stdin, tty=args.tty
            )
        ),
        "env": singleton_only(
            lambda prev: exec(
                *prev.args, tokenize("env"), stdin=args.stdin, tty=args.tty
            )
        ),
        "shell": singleton_only(
            lambda prev: exec(
                *prev.args, tokenize("/bin/sh"), stdin=args.stdin, tty=args.tty
            )
        ),
        "psql": singleton_only(
            lambda prev: exec(
                *prev.args,
                lambda env: ["psql"]
                + (["--quiet", "-t"] if args.quiet else [])
                + ["-U", find_postgres_user(env)],
                stdin=args.stdin,
                tty=args.tty,
                host_env=True,
            )
        ),
        "pg-dump-schema": singleton_only(
            lambda prev: exec(
                *prev.args,
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
            print(f"error: {e}!")
            sys.exit(1)
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
