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
import stat
import shlex
from functools import cached_property
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, TextIO, Optional, Dict, Union, Any, Tuple, Iterable

import rich
import rich.console
import rich.theme


def split_args(*arg_groups: Union[str, List[Any]]) -> List[str]:
    return [
        str(sub_arg)
        for args in arg_groups
        for sub_arg in (shlex.split(args) if isinstance(args, str) else args)
    ]


_THEME = rich.theme.Theme(
    {
        "pgh": "dim bold",
        "info": "blue",
        "succ": "green",
        "skip": "yellow",
        "fail": "red",
        "bold_succ": "bold green",
        "bold_skip": "bold yellow",
        "bold_fail": "bold red",
    }
)

_PGH = "[pgh]pygithooks[/pgh]:"


@dataclass
class Ctx:
    argv: List[str] = field(default_factory=lambda: sys.argv)
    cwd: Path = field(default_factory=Path.cwd)
    env: Dict[str, str] = field(default_factory=lambda: os.environ)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    console: rich.console.Console = field(
        default_factory=lambda: rich.console.Console(file=sys.stderr, theme=_THEME, highlight=False)
    )

    def msg(self, *args, **kwargs):
        self.console.print(*args, **kwargs)

    def out(self, *args, **kwargs):
        kwargs.setdefault("file", self.stderr)
        rich.print(*args, **kwargs)

    def run(self, *args: Union[str, List[Any]], **kwargs) -> subprocess.CompletedProcess:
        kwargs.setdefault("check", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("cwd", self.cwd)
        kwargs.setdefault("env", self.env)
        if not kwargs.get("capture_output"):
            kwargs.setdefault("stdin", self.stdin)
            kwargs.setdefault("stdout", self.stdout)
            kwargs.setdefault("stderr", self.stderr)
        args = split_args(*args)
        return subprocess.run(args, **kwargs)


FILE = Path(__file__).absolute()

HOOK_TEMPLATE = R"""#!/bin/sh
set -eu

sys_exe={sys_exe}
if command -v "$sys_exe" 1>/dev/null 2>&1 ; then
    exec "$sys_exe" {pygithooks} run {hook} -- "$@"
fi

echo "python ($sys_exe) not found, pygithooks not running" 1>&2
exit 0
"""


@dataclass
class GitHook:
    name: str
    args: Tuple[str, ...] = ()


@dataclass
class GitHookScript:
    git_hook: GitHook
    path: Path
    path_full: Path


@dataclass
class CompletedGitHookScript:
    git_hook_script: GitHookScript
    completed_process: subprocess.CompletedProcess

    @staticmethod
    def run(ctx: Ctx, git_hook_script: GitHookScript) -> "CompletedGitHookScript":
        return CompletedGitHookScript(
            git_hook_script, ctx.run([git_hook_script.path_full], check=False, capture_output=True)
        )

    @property
    def succeeded(self) -> bool:
        return self.completed_process.returncode == 0


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

    def run(self, *, hook: str):
        self.ctx.msg(_PGH, f"[bold]{hook}[/bold] hooks running...", style="info")
        self.env_path = self.env_path_with_sys_exe_prefix
        results = [
            CompletedGitHookScript.run(self.ctx, script)
            for script in self.git_hook_scripts(GIT_HOOKS[hook])
        ]

        for result in results:
            if result.succeeded:
                self.ctx.msg(
                    _PGH,
                    f"[bold]{result.git_hook_script.path}[/bold]: [bold]OK[/bold]",
                    style="succ",
                )
            else:
                self.ctx.msg(
                    _PGH,
                    f"[bold]{result.git_hook_script.path}[/bold]: [bold]FAILED[/bold]",
                    style="fail",
                )

            self.ctx.stderr.write(result.completed_process.stderr)
            self.ctx.stdout.write(result.completed_process.stdout)

        all_succeeded = all(result.succeeded for result in results)
        any_succeeded = any(result.succeeded for result in results)
        if any_succeeded and all_succeeded:
            self.ctx.msg(_PGH, f"{hook} hooks SUCCEEDED", style="bold_succ")
            sys.exit(0)
        elif all_succeeded:
            self.ctx.msg(_PGH, f"{hook} hooks SKIPPED", style="bold_skip")
            sys.exit(0)
        else:
            self.ctx.msg(_PGH, f"{hook} hooks FAILED", style="bold_fail")
            sys.exit(1)

    def install(self):
        self.ctx.msg("installing pygithooks into", self.git_hooks_path)
        for hook in GIT_HOOKS.values():
            hook_path = self.git_hooks_path / hook.name
            hook_path.write_text(
                HOOK_TEMPLATE.format(
                    sys_exe=shlex.quote(sys.executable),
                    pygithooks=shlex.quote(FILE.as_posix()),
                    hook=hook.name,
                )
            )
            hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

    def git_hook_scripts(self, git_hook: GitHook) -> Iterable[GitHookScript]:
        top_level = Path(self.pygithooks_path / git_hook.name)
        if top_level.is_dir():
            yield from [
                GitHookScript(git_hook, path.relative_to(self.pygithooks_path), path)
                for path in sorted(top_level.iterdir())
            ]

    def git(self, *args, **kwargs) -> subprocess.CompletedProcess:
        return self.ctx.run("git", *args, **kwargs)

    @property
    def env_path(self) -> str:
        return self.ctx.env.get("PATH", "")

    @env_path.setter
    def env_path(self, path: str):
        self.ctx.env["PATH"] = path

    @property
    def env_path_with_sys_exe_prefix(self) -> str:
        return os.pathsep.join([str(Path(sys.executable).parent.resolve()), self.env_path])

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

    @cached_property
    def git_top_level(self) -> Path:
        return Path(
            self.git("rev-parse --show-toplevel", capture_output=True).stdout.strip()
        ).absolute()

    @cached_property
    def pygithooks_path(self) -> Path:
        return self.git_top_level / ".pygithooks"


def main(ctx: Optional[Ctx] = None):
    ctx = ctx or Ctx()
    parser = argparse.ArgumentParser(
        Path(ctx.argv[0]).name,
        description="TODO",
        allow_abbrev=False,
    )
    parser.set_defaults(action=PyGitHooks.help)
    subparsers = parser.add_subparsers(title="Commands")

    parser_run = subparsers.add_parser(
        "run",
        description="Run a Git hook",
        help="Run a Git hook",
    )
    parser_run.set_defaults(action=PyGitHooks.run)
    parser_run.add_argument("hook", choices=GIT_HOOKS.keys(), help="Hook name as defined by Git")

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
