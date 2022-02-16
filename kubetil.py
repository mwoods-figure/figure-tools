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
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple
from subprocess import run, CompletedProcess

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
        for i, kobj in enumerate(args, start=1):
            # for atom in thing:
            #     sys.stdout.write("{:<40}".format(atom))
            # sys.stdout.write("\n")
            print(f"[{i:>2}]", kobj)

def command(*args, capture: bool = False) -> CompletedProcess:
  return run(args, check=True, capture_output=capture)

def lines(buffer: bytes) -> Iterator[str]:
    for line in buffer.decode("utf-8").split("\n"):
        yield line.strip()

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

def iterable(thing):
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

def singleton_only(f):
    @functools.wraps(f)
    def g(thing):
        if iterable(thing):
            raise ValueError("single value only")
        return f(thing)
    return g

def take(lines: Iterator[str], n: int) -> Optional[str]:
    for i, line in enumerate(lines, start=1):
        if i == n:
            return line
    return None

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

def grep_objects(pat, type_, objects) -> Iterator[KObject]:
    pat_regex = fnmatch.translate(pat)
    return filter_objects(objects, type_, lambda _, ident: re.search(pat_regex, ident, re.I))

def grep_pods(pattern):
    return grep_objects(pattern, "pod", get_all_pods())

def grep_deployments(pattern):
    return grep_objects(pattern, "deployment", get_all_deployments())

def containers(namespace: str, type_: str, ident: str) -> Iterator[str]:
    if type_ == "pod":
        jsonpath = "jsonpath={.spec.containers[*].name}"
    elif type_ == "deployment":
        jsonpath = "jsonpath={.spec.template.spec.containers[*].name}"
    else:
        todo()
    with kubectl("get", "-n", namespace, type_, ident, "-o", jsonpath, capture=True) as lines:
        return head(lines).split(" ")

def exec(namespace: str, type_: str, ident: str, cmd: str):
    cs = containers(namespace, type_, ident)
    main_container = head([c for c in cs if "linkerd" not in c])
    container_args = ["-c", main_container] if len(main_container) > 0 else []
    with kubectl("exec", "--stdin", "--tty", "-n", namespace, f"{type_}/{ident}", *container_args, "--", cmd) as cp:
        return cp

def todo():
    raise NotImplementedError

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", default=False)

    def build_index_args(parser):
        for i in range(1, 10):
            parser.add_argument(f"-{i}", action="store_const", const=i, required=False, dest="index")

    def build_verbs(parser):
        p = parser.add_subparsers(help="verb", dest="verb")
        verbs = [
            NV("show-containers", "Show containers"),
            NV("exec", "Shell operations", [("command", dict(help="Command to run"))])
        ]
        for v in verbs:
            pp = p.add_parser(v.name, help=f"Verb: {v.description}")
            for name, kws in  v.arguments:
                pp.add_argument(name, **kws)
            # build_index_args(pp)

    def build_nouns(parser):
        p = parser.add_subparsers(help="noun", dest="noun")
        nouns = [
            NV("pods", "Pod operations", [("pattern", dict(help="Glob pattern"))]),
            NV("deployments", "Deployment operations", [("pattern", dict(help="Glob pattern"))])
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
    print(args)

    do_next = [args.noun, args.verb]
    actions = {
        "pods": lambda prev: grep_pods(args.pattern),
        "deployments": lambda prev: grep_deployments(args.pattern),
        "show-containers": singleton_only(lambda prev: containers(*prev.args)),
        "exec": singleton_only(lambda prev: exec(*prev.args, args.command)),
    }

    output, code, index = None, 0, args.index

    while len(do_next) > 0:
        if (thing_to_do := do_next.pop(0)) is None:
            break
        output = actions[thing_to_do](output)
        if index is not None:
            if iterable(output):
                output = take(output, index)
            else:
                output = None
            index = None

    if output is not None:
        if iterable(output):
            display(*output)
        else:
            display(output)
    else:
        code = 1

    sys.exit(code)

