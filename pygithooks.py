#!/bin/sh
# syntax: -*- Python -*-
""":"
set -eu
for python in python3 python py ; do
    if command -v "$python" >/dev/null ; then
        exec "$python" "$0" "$@"
    fi
done

echo "python not found, pygithooks not running" 1>&2
exit 1
":"""

import sys

if sys.version_info < (3, 8):
    sys.stderr.write("%s must run with Python 3.8+, not %s\n" % (__file__, sys.version))
    sys.exit(1)


import argparse
import os
import subprocess
import shlex
from functools import cached_property
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, TextIO, Optional, Dict, Union, Any, Tuple


def split_args(*arg_groups: Union[str, List[Any]]) -> List[str]:
    return [
        str(sub_arg)
        for args in arg_groups
        for sub_arg in (shlex.split(args) if isinstance(args, str) else args)
    ]


@dataclass
class Ctx:
    argv: List[str] = field(default_factory=lambda: sys.argv)
    cwd: Path = field(default_factory=Path.cwd)
    env: Dict[str, str] = field(default_factory=lambda: os.environ)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)

    def msg(self, *args, **kwargs):
        kwargs.setdefault("file", self.stderr)
        print(*args, **kwargs)

    def out(self, *args, **kwargs):
        kwargs.setdefault("file", self.stderr)
        print(*args, **kwargs)

    def run(self, *args: Union[str, List[Any]], **kwargs) -> subprocess.CompletedProcess:
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("env", self.env)
        kwargs.setdefault("cwd", self.cwd)
        args = split_args(*args)
        return subprocess.run(args, **kwargs)


FILE = Path(__file__).absolute()

HOOK_TEMPLATE = R"""#!/bin/sh
set -eu

sys_exe={sys_exe}
if command -v "$sys_exe" ; then
    exec "$sys_exe" {pygithooks} exec {hook} "$@"
fi

echo "python ($sys_exe) not found, pygithooks not running" 1>&2
exit 0
"""


@dataclass
class GitHook:
    name: str
    args: Tuple[str, ...] = ()


GIT_HOOKS: Dict[str, GitHook] = {
    "pre-commit": GitHook("pre-commit"),
}


@dataclass
class PyGitHooks:
    ctx: Ctx
    parser: argparse.ArgumentParser

    def main(self):
        args = vars(self.parser.parse_args(self.ctx.argv[1:]))
        action = args.pop("action")
        action(self, **args)

    def help(self):
        self.parser.print_help(self.ctx.stderr)

    def exec(self, *, hook: str):
        self.ctx.msg("running hook", hook)

    def install(self):
        self.ctx.msg("installing pygithooks into", self.git_hooks_path)
        for hook in GIT_HOOKS.values():
            (self.git_hooks_path / hook.name).write_text(
                HOOK_TEMPLATE.format(
                    sys_exe=shlex.quote(sys.executable),
                    pygithooks=shlex.quote(FILE.as_posix()),
                    hook=hook.name)
            )

    def git(self, *args, **kwargs) -> subprocess.CompletedProcess:
        return self.ctx.run("git", *args, **kwargs)

    @cached_property
    def git_dir(self) -> Path:
        return Path(self.git("rev-parse --git-dir", capture_output=True).stdout.strip())

    @cached_property
    def git_hooks_path(self) -> Path:
        return Path(
            self.git(
                "config --get --default",
                [self.git_dir / "hooks"],
                "core.hooksPath",
                capture_output=True,
            ).stdout.strip()
        )


def main(ctx: Optional[Ctx] = None):
    ctx = ctx or Ctx()
    parser = argparse.ArgumentParser(
        Path(ctx.argv[0]).name,
        description="TODO",
        allow_abbrev=False,
    )
    parser.set_defaults(action=PyGitHooks.help)
    subparsers = parser.add_subparsers(title="Commands")

    parser_exec = subparsers.add_parser(
        "exec",
        description="Execute a Git hook",
        help="Execute a Git hook",
    )
    parser_exec.set_defaults(action=PyGitHooks.exec)
    parser_exec.add_argument("hook", help="Hook name as defined by Git")

    parser_install = subparsers.add_parser(
        "install",
        description="Install pygithooks in Git project",
        help="Install pygithooks in Git project",
    )
    parser_install.set_defaults(action=PyGitHooks.install)

    py_git_hooks = PyGitHooks(ctx, parser)
    py_git_hooks.main()


if __name__ == "__main__":
    sys.exit(main())
