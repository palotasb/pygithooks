#!/bin/sh
# syntax: -*- Python -*-
""":"
if command -v python3 >/dev/null ; then
    exec python3 "$0" "$@"
elif command -v python >/dev/null ; then
    exec python "$0" "$@"
fi

echo "python not found, pygithooks not running" 1>&2

if [ "{$1:-}" = "exec" ] ; then
    exit 0
fi

exit 1
":"""

import sys

if sys.version_info < (3, 8):
    sys.stderr.write("%s must run with Python 3.8+, not %s\n" % (__file__, sys.version))
    sys.exit(1)


import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, TextIO, Optional, Dict


@dataclass
class Ctx:
    argv: List[str] = field(default_factory=lambda: sys.argv)
    cwd: Path = field(default_factory=Path.cwd)
    env: Dict[str, str] = field(default_factory=lambda: os.environ)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)


@dataclass
class PyGitHooks:
    ctx: Ctx
    parser: argparse.ArgumentParser
    git_dir: Optional[Path] = None

    def main(self):
        args = vars(self.parser.parse_args(self.ctx.argv[1:]))
        self.git_dir = args.pop("git_dir", None)
        action = args.pop("action")
        action(self, **args)

    def help(self):
        self.parser.print_help(self.ctx.stderr)

    def exec(self, *, hook: str):
        self.ctx.stdout.write(f"{hook}\n")

    def install(self):
        self.ctx.stdout.write(f"install into {self.git_dir}\n")


def main(ctx: Optional[Ctx] = None):
    ctx = ctx or Ctx()
    parser = argparse.ArgumentParser(
        Path(ctx.argv[0]).name,
        description="TODO",
        allow_abbrev=False,
    )
    parser.set_defaults(action=PyGitHooks.help)
    # TODO --chdir/-C
    # TODO --git-work-tree/GIT_WORK_TREE (?)
    # TODO --git-hooks-path/git config core.hooksPath
    parser.add_argument("--git-dir", type=Path, help="Path to repository (\".git\" directory)", default=ctx.env.get("GIT_DIR"))
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
