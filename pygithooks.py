import argparse
import os
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, Tuple, Union

import rich
import rich.console
import rich.theme

FILE = Path(__file__).absolute()

HOOK_TEMPLATE = R"""#!/bin/sh
set -eu

sys_exe={sys_exe}
if command -v "$sys_exe" 1>/dev/null 2>&1 ; then
    exec "$sys_exe" {pygithooks} run {hook} -- "$@"
fi

echo "pygithooks: python ($sys_exe) not found, no hooks running" 1>&2
exit 0
"""

_THEME = rich.theme.Theme(
    {
        "info": "blue",
        "success": "green",
        "skip": "yellow",
        "fail": "red",
    }
)

_PGH = "[dim bold]pygithooks[/dim bold]:"


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
    env: Dict[str, str] = field(default_factory=lambda: dict(os.environ))
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    console: rich.console.Console = field(
        default_factory=lambda: rich.console.Console(file=sys.stderr, theme=_THEME, highlight=False)
    )

    def msg(self, *args, **kwargs):
        self.console.print(_PGH, *args, **kwargs)

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
        args_list = split_args(*args)
        return subprocess.run(args_list, **kwargs)


@dataclass
class GitHook:
    name: str
    args: Tuple[str, ...] = ()


@dataclass
class GitHookScript:
    git_hook: GitHook
    name: str
    path_full: Path


@dataclass
class CompletedGitHookScript:
    git_hook_script: GitHookScript
    completed_process: Optional[subprocess.CompletedProcess]

    @property
    def skipped(self) -> bool:
        return self.completed_process is None

    @property
    def succeeded(self) -> bool:
        return self.completed_process is not None and self.completed_process.returncode == 0


GIT_HOOKS: Dict[str, GitHook] = {
    "pre-commit": GitHook("pre-commit"),
}


@dataclass
class PyGitHooks:
    ctx: Ctx
    parser: argparse.ArgumentParser

    def main(self) -> None:
        args = vars(self.parser.parse_args(self.ctx.argv[1:]))
        chdir: Path
        if chdir := args.pop("chdir", None):
            os.chdir(chdir)

        action = args.pop("action")
        action(self, **args)

    def help(self):
        self.parser.print_help(self.ctx.stderr)

    def _run_git_hook_script(self, git_hook_script: GitHookScript) -> CompletedGitHookScript:
        completed_process: Optional[subprocess.CompletedProcess] = None
        try:
            completed_process = self.ctx.run(
                [git_hook_script.path_full], check=False, capture_output=True
            )
        except OSError:
            pass

        return CompletedGitHookScript(git_hook_script, completed_process)

    def run(self, *, hook: str):
        self.ctx.msg(f"[bold]{hook}[/bold] hooks running...", style="info")
        self.env_path = self.env_path_with_sys_exe_prefix

        results: List[CompletedGitHookScript] = []
        for git_hook_script in self.git_hook_scripts(GIT_HOOKS[hook]):
            result = self._run_git_hook_script(git_hook_script)
            results.append(result)

            if result.succeeded:
                self.ctx.msg(
                    f"[bold]{result.git_hook_script.name}[/bold]: [bold]OK[/bold]", style="success"
                )
            elif result.skipped:
                self.ctx.msg(
                    f"[bold]{result.git_hook_script.name}[/bold]: [bold]SKIPPED[/bold]",
                    style="skip",
                )
            else:
                self.ctx.msg(
                    f"[bold]{result.git_hook_script.name}[/bold]: [bold]FAILED[/bold]", style="fail"
                )

            if result.completed_process:
                self.ctx.stderr.write(result.completed_process.stderr)
                self.ctx.stdout.write(result.completed_process.stdout)

        all_succeeded = all(result.succeeded or result.skipped for result in results)
        any_succeeded = any(result.succeeded for result in results)
        if any_succeeded and all_succeeded:
            self.ctx.msg(f"{hook} hooks SUCCEEDED", style="bold green")
            sys.exit(0)
        elif all_succeeded:
            self.ctx.msg(f"{hook} hooks SKIPPED", style="bold yellow")
            sys.exit(0)
        else:
            self.ctx.msg(f"{hook} hooks FAILED", style="bold red")
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
                GitHookScript(git_hook, path.relative_to(self.pygithooks_path).as_posix(), path)
                for path in sorted(top_level.iterdir())
                if path.stat().st_mode & stat.S_IEXEC == stat.S_IEXEC
            ]

    def run_git(self, *args, **kwargs) -> subprocess.CompletedProcess:
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
        return Path(self.run_git("rev-parse --git-dir", capture_output=True).stdout.strip())

    @cached_property
    def git_hooks_path(self) -> Path:
        return Path(
            self.run_git(
                "config --get --default",
                [self.git_dir / "hooks"],
                "core.hooksPath",
                capture_output=True,
            ).stdout.strip()
        )

    @cached_property
    def git_top_level(self) -> Path:
        return Path(
            self.run_git("rev-parse --show-toplevel", capture_output=True).stdout.strip()
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
    parser.add_argument("--chdir", "-C", metavar="DIR", type=Path, help="chdir to this directory")
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
