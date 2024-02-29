import argparse
import contextlib
import os
import shlex
import stat
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, TextIO, Tuple, Union

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


class PyGitHooksUsageError(Exception):
    pass


@dataclass
class Ctx:
    stack: contextlib.ExitStack = field(default_factory=contextlib.ExitStack)
    argv: List[str] = field(default_factory=lambda: sys.argv)
    cwd: Path = field(default_factory=Path.cwd)
    env: Dict[str, str] = field(default_factory=lambda: dict(os.environ))
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    console: rich.console.Console = field(
        default_factory=lambda: rich.console.Console(file=sys.stderr, theme=_THEME, highlight=False)
    )
    verbose: bool = True

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
    parser: argparse.ArgumentParser = field(init=False)
    verbose: bool = field(init=False)
    git_repo: Path = field(init=False)
    git_dir: Path = field(init=False)
    action: Callable = field(init=False)
    args: Dict[str, Any] = field(init=False)

    def __post_init__(self):
        self.parser = argparse.ArgumentParser(
            Path(self.ctx.argv[0]).name,
            description="TODO",
            allow_abbrev=False,
        )
        self.parser.set_defaults(action=self.help)
        self.parser.add_argument("-v", "--verbose", action="store_true", help="more verbose output")
        self.parser.add_argument(
            "-C",
            "--chdir",
            metavar="DIR",
            type=Path,
            help="change the current working directory to DIR",
        )
        self.parser.add_argument(
            "-g",
            "--git-repo",
            metavar="DIR",
            type=Path,
            help="use DIR as the git repo instead of the working directory",
        )
        self.parser.add_argument(
            "-G", "--git-dir", metavar="DIR", type=Path, help="use DIR as the .git directory"
        )
        subparsers = self.parser.add_subparsers(title="Commands")

        parser_run = subparsers.add_parser(
            "run",
            description="Run a Git hook",
            help="Run a Git hook",
        )
        parser_run.set_defaults(action=self.run)
        parser_run.add_argument(
            "hook", choices=GIT_HOOKS.keys(), help="Hook name as defined by Git"
        )

        parser_install = subparsers.add_parser(
            "install",
            description="Install pygithooks in Git project",
            help="Install pygithooks in Git project",
        )
        parser_install.set_defaults(action=self.install)

        self.args = vars(self.parser.parse_args(self.ctx.argv[1:]))

        self.ctx.verbose = self.args.pop("verbose")
        if self.ctx.verbose:
            self.ctx.msg("running in verbose mode")

        chdir: Path
        if chdir := self.args.pop("chdir", None):
            chdir = chdir.absolute()
            if chdir.is_dir():
                self.ctx.cwd = chdir
                self.ctx.stack.enter_context(contextlib.chdir(chdir))
            else:
                raise PyGitHooksUsageError(
                    f"not a directory: {chdir}",
                    "`cd` into a directory and omit the `--chdir DIR` option, or choose a valid DIR value.",
                )
        if self.ctx.verbose:
            self.ctx.msg("cwd:", self.ctx.cwd)

        git_repo: Path | None = self.args.pop("git_repo", None)
        self.git_repo = git_repo if git_repo else self._default_git_repo()
        if self.ctx.verbose:
            self.ctx.msg("git repo:", self.git_repo)

        git_dir: Path | None = self.args.pop("git_dir", None)
        self.git_dir = git_dir if git_dir else self.git_repo / ".git"
        if self.ctx.verbose:
            self.ctx.msg("git dir:", self.git_dir)

        self.action = self.args.pop("action")

    def _default_git_repo(self) -> Path:
        for path in [self.ctx.cwd] + list(self.ctx.cwd.parents):
            if (path / ".git").is_dir():
                return path

        raise PyGitHooksUsageError(
            f"Could not find a git repo here: {self.ctx.cwd}",
            "`cd` into a git repo, or use one of these CLI options: `--chdir DIR`, `--git-repo DIR`.",
        )

    def main(self) -> None:
        self.action(**self.args)

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
    def pygithooks_path(self) -> Path:
        return self.git_repo / ".pygithooks"


@contextlib.contextmanager
def basic_error_handler():
    try:
        yield
    except Exception as err:
        print(
            "pygithooks: INTERNAL ERROR:", err.__class__.__name__, "-", *err.args, file=sys.stderr
        )
        print("pygithooks: This is a bug, please report it.", file=sys.stderr)
        print("pygithooks:", *traceback.format_exception(err), sep="\n", file=sys.stderr)
        sys.exit(2)


@contextlib.contextmanager
def fancy_ctx_aware_error_handler(ctx: Ctx):
    try:
        yield
    except PyGitHooksUsageError as err:
        ctx.msg("ERROR:", err.args[0], style="bold red")
        ctx.msg("Potential solutions to this error:", err.args[1], style="yellow")
        ctx.msg("Otherwise this is a bug, please report it.", style="yellow")
        sys.exit(1)
    except Exception as err:
        ctx.msg("INTERNAL ERROR:", err.__class__.__name__, "-", *err.args, style="bold red")
        ctx.msg("This is a bug, please report it.", style="red")
        if ctx.verbose:
            ctx.msg(*traceback.format_exception(err), sep="\n", style="yellow")
        else:
            ctx.msg("For more error info, re-run with the `--verbose` CLI option.", style="yellow")
        sys.exit(2)


def main(stack: contextlib.ExitStack | None = None, ctx: Ctx | None = None):
    stack = stack or contextlib.ExitStack()
    with stack:
        stack.enter_context(basic_error_handler())
        ctx = ctx or Ctx(stack)
        stack.enter_context(fancy_ctx_aware_error_handler(ctx))
        assert stack is ctx.stack
        py_git_hooks = PyGitHooks(ctx)
        py_git_hooks.main()


if __name__ == "__main__":
    sys.exit(main())
