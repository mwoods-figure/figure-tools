#!/usr/bin/env python

import asyncio
import argparse
from contextlib import contextmanager
from collections.abc import Iterable
import json as pyjson
import sys
import shlex
import json
from itertools import islice
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union
from subprocess import Popen, PIPE, STDOUT
import logging
import sys
import inspect
import pprint


log = logging.getLogger("glue")


@contextmanager
def color(*names):
    reset = '\033[0m'
    colors = {
        "black": 30,
        "red": 31,
        "green": 32,
        "yellow": 33,
        "blue": 34,
        "magenta": 35,
        "cyan": 36,
        "white": 37
    }
    def colorize(name):
        color = colors[name]
        code = f"\033[{color}m"
        return lambda *text: f"{code}{''.join(text)}{reset}"
    yield [colorize(name) for name in names]


@dataclass(repr=False)
class KObject:
    line: str
    type_: str
    headers: List[str]
    attributes: Dict[str, str] = field(init=False)

    @classmethod
    def pod(cls, line, headers):
        return cls(line, "pod", headers)

    @classmethod
    def deployment(cls, line, headers):
        return cls(line, "deployment", headers)

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

    def __getitem__(self, key):
        return self.attributes[key]

    def __getattr__(self, name):
        return self.attributes[name]

    def __repr__(self) -> str:
        return f"{self.namespace}:{self.name}"

    def __str__(self) -> str:
        return self.line


@contextmanager
def kubectl(*args, **kwargs):
    stdout = kwargs.pop("stdout") if "stdout" in kwargs else PIPE
    with color("green") as (c,):
        log.warning(f"{c('EXECUTE')}: kubectl {' '.join(args)}")
    with Popen(["kubectl", *args], stdout=stdout, **kwargs) as proc:
        yield proc


def error(message):
    raise Exception(message)


def cond_arg(test, *args, default=[]):
    return args if test else default


@contextmanager
def fzf(pipe_in, *args, query=None, tac=False, **kwargs):
    text = kwargs.pop("text") if "text" in kwargs else True
    with_query = cond_arg(query, "--query")
    with_tac = cond_arg(tac, "--tac")
    with Popen(
        [
            "fzf",
            "--cycle",
            "--multi",
            "--layout=reverse-list",
            *with_tac,
            "--height=~50%",
            *with_query,
            *args,
        ],
        stdin=pipe_in,
        stdout=PIPE,
        text=text,
        **kwargs,
    ) as proc:
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


def first(thing):
    if iterable(thing):
        return next(iter(thing), None)
    return thing


def skip(lines: Iterator[str], n: int) -> Iterator[str]:
    return next(islice(lines, n, n))


def take(lines: Iterator[str], n: int) -> Optional[str]:
    for i, line in enumerate(lines, start=1):
        if i == n:
            return line
    return None


def lines(thing: Union[bytes, Iterable[str]]) -> Iterator[str]:
    if isinstance(thing, bytes):
        for line in thing.decode("utf-8").split("\n"):
            yield line.strip()
    elif iterable(thing):
        for line in thing:
            yield line
    else:
        return iter([thing])


def unlines(pieces: Iterable[Union[str, bytes]]) -> str:
    return "\n".join(
        piece.decode("utf-8") if isinstance(piece, bytes) else piece for piece in pieces
    )


def columns(line: str) -> Iterable[str]:
    for column in line.split():
        yield column


def column(lines: Iterator[str], index: int) -> Iterator[str]:
    for line in lines:
        yield next(take(columns(line), index))


def pick(*items: str) -> str:
    with fzf(PIPE) as p:
        out, err = p.communicate(unlines(reversed(items)))
        if err:
            error(err)
    return out.strip()


def pretty_json(thing):
    if isinstance(thing, str):
        out = json.loads(thing)
    else:
        out = thing
    print(json.dumps(out, indent=4))


def find_postgres_user(env: Dict[str, str]) -> str:
    candidates = ["POSTGRES_USER", "PG_USER", "DB_USER"]
    for candidate in candidates:
        if candidate in env:
            return env[candidate]
    error("env:no postgres user found")


async def choose_namespace() -> Iterator[str]:
    """
    List all namespaces, picking one.
    """
    headers = [
        "name",
        "status",
        "age",
    ]

    with kubectl("get", "namespaces") as p1:
        with fzf(p1.stdout) as p2:
            if not p2.stdout:
                error("Missing stdout")
            #await skip(lines(p2.stdout), 1)


async def choose_pods(
    namespace: Optional[str] = None, selector: Optional[str] = None
) -> Iterator[KObject]:
    """
    List and choose pods.
    """
    headers = [
        "namespace",
        "name",
        "container-images",
    ]

    custom_headers = """custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,CONTAINER-IMAGES:.spec.containers[*].image"""

    with kubectl(
        "get",
        "pods",
        *cond_arg(namespace, "-n", namespace, default=["-A"]),
        "--no-headers=true",
        *["-o", custom_headers],
        *cond_arg(selector, f"--selector={selector}"),
        text=True,
    ) as p1:
        with fzf(p1.stdout) as p2:
            if not p2.stdout:
                error("Missing stdout")
            return iter([KObject.pod(line, headers) for line in p2.stdout])


async def choose_deployments(namespace: Optional[str] = None) -> Iterator[KObject]:
    """
    List and choose deployments.
    """
    headers = [
        "namespace",
        "name",
    ]

    custom_headers = (
        """custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name"""
    )

    with kubectl(
        "get",
        "deployments",
        *cond_arg(namespace, "-n", namespace, default=["-A"]),
        "--no-headers=true",
        *["-o", custom_headers],
        text=True,
    ) as p1:
        with fzf(p1.stdout) as p2:
            if not p2.stdout:
                error("Missing stdout")
            return iter([KObject.deployment(line, headers) for line in p2.stdout])


async def choose_containers(kobj: KObject) -> Iterator[str]:
    """
    Given a [KObject], list containers and choose one.
    """
    if kobj.is_pod:
        jsonpath = "jsonpath={.spec.containers[*].name}"
    elif kobj.is_deployment:
        jsonpath = "jsonpath={.spec.template.spec.containers[*].name}"
    else:
        return iter([])
    with kubectl(
        "get", "-n", kobj.namespace, kobj.type_, kobj.name, "-o", jsonpath
    ) as p1:
        if not p1.stdout:
            error("Missing stdout")
        containers = unlines(columns(first(p1.stdout)))
        with fzf(PIPE) as p2:
            containers_line, err = p2.communicate(containers)
            if err is not None:
                error(err)
            return iter(c.strip() for c in containers_line.split("\n"))


async def command_db(args, remaining):
    """
    Connect to a Figure database and open a psql prompt.
    """
    namespace = args.get("namespace")
    selector = args.get("selector")
    tail = args.get("tail", False)
    choice = pick("pod", "deployment")
    if choice == "pod":
        chosen = await choose_pods(namespace, selector)
    elif choice == "deployment":
        chosen = await choose_deployments(namespace)
    else:
        raise NotImplementedError

    kobj = first(chosen)
    containers = await choose_containers(kobj)
    container = first(containers)

    env = await command_env(args, remaining, kobj=kobj, container=container, quiet=True)
    db_user = find_postgres_user(env)

    args["cmd"] = f"""/usr/bin/psql -U {db_user}"""
    args["stdin"] = True
    args["tty"] = True

    await command_exec(args, remaining, kobj=kobj, container=container)


async def command_exec(args, remaining, kobj=None, container=None, stdout=None, f=None):
    """
    Execute a command on a pod.
    """
    namespace = args.get("namespace")
    selector = args.get("selector")
    tty = (args.get("tty"),)

    if not kobj:
        pods = await choose_pods(namespace, selector)
        kobj = first(pods)
    if not container:
        containers = await choose_containers(kobj)
        container = first(containers)

    cmd = args.get("cmd")
    cmd_tokens = cmd if iterable(cmd) else shlex.split(cmd)

    with kubectl(
        "exec",
        *cond_arg(args.get("stdin"), "--stdin"),
        *cond_arg(tty, "--tty"),
        *["-n", kobj.namespace],
        kobj.to_specifier(),
        *["-c", container],
        "--",
        *cmd_tokens,
        stdout=stdout,
        text=tty,
    ) as p:
        if callable(f):
            return f(p)
        else:
            return None


async def command_env(args, remaining, kobj=None, container=None, quiet=False):
    """
    Print environment variables
    """
    args["cmd"] = "/bin/env"

    def collect(proc):
        e = {}
        for line in lines(proc.stdout):
            line = line.strip()
            parts = line.split("=")
            e[parts[0]] = parts[1]
        return e

    e = await command_exec(
        args, remaining, kobj=kobj, container=container, stdout=PIPE, f=collect
    )

    if not quiet:
        if args.get("json"):
            pretty_json(e)
        else:
            pprint.pprint(e)

    return e


async def command_logs(args, remaining, kobj=None, container=None):
    """
    Stream kubectl logs.
    """
    namespace = args.get("namespace")
    selector = args.get("selector")
    tail = args.get("tail", False)

    if not kobj:
        choice = pick("pod", "deployment")
        if choice == "pod":
            chosen = await choose_pods(namespace, selector)
        elif choice == "deployment":
            chosen = await choose_deployments(namespace)
        else:
            raise NotImplementedError
        kobj = first(chosen)
    if not container:
        containers = await choose_containers(kobj)
        container = first(containers)

    with kubectl(
        "logs",
        "-n",
        kobj.namespace,
        kobj.to_specifier(),
        "-f",
        "-c",
        container,
        stdout=PIPE,
        text=True,
    ) as p:
        while True:
            p.poll()
            if p.stdout:
                for line in p.stdout:
                    try:
                        pretty_json(line.strip())
                    except Exception as e:
                        print(line.strip())
            if not tail:
                break
        if p.stdout:
            p.stdout.close()


async def command_port_forward(args, remaining, kobj=None, container=None):
    pass


async def command_shell(args, remaining, kobj=None, container=None):
    """
    Execute a shell.
    """
    args["cmd"] = "/bin/bash"
    args["stdin"] = True
    args["tty"] = True
    await command_exec(args, remaining, kobj=kobj, container=container)


###############################################################################


def setup_args() -> Tuple[str, Dict[str, Any], List[str]]:
    def setup_global_args(parser, suppress=False):
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS if suppress else False,
            dest="verbose",
            help="Include more output",
        )
        parser.add_argument(
            "-n",
            "--namespace",
            default=argparse.SUPPRESS if suppress else None,
            dest="namespace",
            help="Namespace to use",
        )
        return parser

    def setup_db(parser):
        p = parser.add_parser(
            "db", description="Connect to a Figure database and open a psql prompt"
        )
        setup_global_args(p, suppress=True)

    def setup_env(parser):
        p = parser.add_parser("env", description="Display environment variables")
        p.add_argument(
            "--json", action="store_true", default=True, help="Output as JSON"
        )
        setup_global_args(p, suppress=True)

    def setup_exec(parser):
        p = parser.add_parser("exec", description="Execute a command on a pod")
        setup_global_args(p, suppress=True)
        p.add_argument("cmd", help="A command to run")
        p.add_argument(
            "-t", "--tty", action="store_true", dest="tty", help="Enable tty"
        )
        p.add_argument(
            "-i", "--stdin", action="store_true", dest="stdin", help="Enable stdin"
        )

    def setup_logs(parser):
        p = parser.add_parser("logs", description="Stream kubectl logs")
        setup_global_args(p, suppress=True)
        p.add_argument(
            "-f",
            "--tail",
            action="store_true",
            default=argparse.SUPPRESS,
            dest="tail",
            help="Tail output",
        )

    def setup_shell(parser):
        p = parser.add_parser("shell", description="Open a shell")
        setup_global_args(p, suppress=True)

    root_parser = argparse.ArgumentParser()
    setup_global_args(root_parser)
    commands = root_parser.add_subparsers(
        title="Commands", dest="command", required=True
    )

    setup = [setup_db, setup_env, setup_exec, setup_logs, setup_shell]
    for f in setup:
        f(commands)

    args, remaining = root_parser.parse_known_args()
    args = vars(args)

    return str(args["command"]), args, remaining


async def runner(command: str, args: Dict[str, Any], remaining: list[str]):

    verbose = args.get("verbose", False)
    namespace = args.get("namespace")

    if verbose:
        logging.basicConfig(level=logging.INFO)

    if not namespace:
        await choose_namespace()

    # command_functions = {
    #     name: obj
    #     for (name, obj) in inspect.getmembers(sys.modules[__name__])
    #     if (
    #         inspect.isfunction(obj)
    #         and name.startswith("command_")
    #         and obj.__module__ == __name__
    #     )
    # }
    # command_name = f"command_{command}"
    # if command_name not in command_functions:
    #     print(f"Invalid command: {command}")
    #     sys.exit(1)
    #
    # f = command_functions[command_name]
    # await f(args, remaining)

    sys.exit(0)


if __name__ == "__main__":
    command, args, remaining = setup_args()
    asyncio.run(runner(command, args, remaining))
