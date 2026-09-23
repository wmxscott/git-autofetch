"""Command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from git_autofetch import __version__, agent, paths
from git_autofetch.config import Config, ConfigError, load
from git_autofetch.cycle import CycleReport, run_cycle
from git_autofetch.repos import discover
from git_autofetch.ssh import CONTROL_NAME, SshOptions
from git_autofetch.state import Lock, LockHeldError, State
from git_autofetch.system import System, build_env

log = logging.getLogger("git_autofetch")

# How long `run` waits before looking again at a missing or broken config, and
# the longest it sleeps between checks, so config changes apply promptly.
RECHECK_SECONDS = 30
# Plugging a key in can fire the resume agent more than once.
RESUME_COOLDOWN_SECONDS = 60


@dataclass
class Context:
    config_path: Path
    config: Config | None
    state: State
    lock_path: Path

    def system(self, env: Mapping[str, str]) -> System:
        assert self.config is not None
        control_path = self.config.control_path
        if control_path:
            control_path = str(Path(control_path).expanduser())
        else:
            control_path = str(paths.ensure_private_dir(paths.control_dir()) / CONTROL_NAME)
        ssh = SshOptions(control_path, self.config.control_persist)
        return System(ssh, build_env(self.config, env))


def load_context(env: Mapping[str, str]) -> Context:
    config_path = paths.config_file(env)
    state_dir = paths.state_dir(env)
    return Context(config_path, load(config_path), State(state_dir), state_dir / "lock")


def _home(path: str | Path) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text == home or text.startswith(home + "/") else text


def _cycle(context: Context, env: Mapping[str, str]) -> CycleReport:
    assert context.config is not None
    report = run_cycle(context.config, context.system(env), context.state)
    context.state.save()
    return report


def _print_report(report: CycleReport) -> int:
    for host in report.hosts:
        print(f"{host.target}: {host.reason}")
    for result in report.results:
        detail = f"  ({result.detail})" if result.detail else ""
        print(f"{result.status:<9} {_home(result.repo)}{detail}")
    failed = [r for r in report.results if r.status not in ("ok", "skipped")]
    return 1 if failed else 0


def _require_config(context: Context) -> Config | None:
    if context.config is None:
        print(f"no config at {_home(context.config_path)}", file=sys.stderr)
    return context.config


# -- commands -----------------------------------------------------------------


def cmd_run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    log.info("git-autofetch %s started", __version__)
    reported = ""
    while True:
        try:
            context = load_context(env)
            if context.config is None:
                raise ConfigError(f"no config at {context.config_path}; waiting for one")
            interval = context.config.interval
            with contextlib.suppress(LockHeldError), Lock(context.lock_path):
                context.state.reload()
                if context.state.due(interval):
                    _cycle(context, env)
            reported = ""
            wait = context.state.seconds_until_due(interval)
        except (ConfigError, paths.UnsafeDirectoryError) as error:
            if str(error) != reported:
                log.error("%s", error)
                reported = str(error)
            wait = RECHECK_SECONDS
        time.sleep(min(max(wait, 1), RECHECK_SECONDS))


def cmd_fetch(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    context = load_context(env)
    if not _require_config(context):
        return 1
    try:
        with Lock(context.lock_path):
            context.state.reload()
            return _print_report(_cycle(context, env))
    except LockHeldError:
        print("another fetch is running")
        return 0


def cmd_resume(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    context = load_context(env)
    if not _require_config(context):
        return 1
    try:
        with Lock(context.lock_path):
            context.state.reload()
            if context.state.clock() - context.state.last_resume < RESUME_COOLDOWN_SECONDS:
                print("resumed less than a minute ago; nothing to do")
                return 0
            context.state.clear_all_snoozes()
            context.state.mark_resume()
            return _print_report(_cycle(context, env))
    except LockHeldError:
        print("another fetch is running")
        return 0


def cmd_status(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    context = load_context(env)
    print(f"git-autofetch {__version__}")
    found = "found" if context.config else "missing"
    print(f"config:       {_home(context.config_path)} ({found})")
    config = context.config
    if config is None:
        return 1
    state = context.state
    system = context.system(env)

    last = state.last_cycle
    if state.last_run:
        when = datetime.fromtimestamp(state.last_run).strftime("%Y-%m-%d %H:%M:%S")
        took = f", took {last['duration']}s" if last else ""
        due_in = int(state.seconds_until_due(config.interval))
        print(f"last run:     {when}{took}; next in {due_in}s (every {config.interval}s)")
    else:
        print(f"last run:     never (every {config.interval}s)")

    if config.hardware_key:
        present = system.hardware_key_present(config.hardware_key)
        print(
            f"hardware key: {config.hardware_key} ({'plugged in' if present else 'not plugged in'})"
        )

    hosts = last.get("hosts", [])
    if hosts:
        print("hosts:")
        for host in hosts:
            print(f"  {host['target']}: {host['reason']}")
    snoozes = {t: s for t in state.data.get("snooze", {}) if (s := state.snooze(t))}
    for target, entry in snoozes.items():
        until = datetime.fromtimestamp(entry["until"]).strftime("%H:%M:%S")
        print(f"  {target}: waiting until {until} ({entry.get('reason', '')})")

    results = last.get("results", [])
    if results:
        print("repositories:")
        for result in results:
            detail = f"  ({result['detail']})" if result.get("detail") else ""
            print(f"  {result['status']:<9} {_home(result['repo'])}{detail}")
    return 0


def cmd_list_repos(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    context = load_context(env)
    config = _require_config(context)
    if config is None:
        return 1
    system = context.system(env)
    for repo in discover(config.repos):
        hosts = sorted({str(r.target) for r in system.remotes(repo) if r.target})
        print(f"{repo}" + (f"  [{', '.join(hosts)}]" if hosts else ""))
    return 0


def cmd_install_resume_agent(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    context = load_context(env)
    config = _require_config(context)
    if config is None:
        return 1
    if not config.hardware_key:
        print("set hardware_key in the config first", file=sys.stderr)
        return 1
    if args.print:
        sys.stdout.write(agent.resume_agent(config.hardware_key, agent.executable()).decode())
        return 0
    path = agent.install(config.hardware_key)
    print(f"installed {_home(path)}")
    return 0


def cmd_uninstall_resume_agent(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    removed = agent.uninstall()
    print("removed" if removed else "not installed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="git-autofetch",
        description="Keep git remotes fresh in the background without surprise login prompts.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"git-autofetch {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every step")
    commands = parser.add_subparsers(dest="command", metavar="command")

    def add(name: str, func, help: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, description=help)
        sub.set_defaults(func=func)
        return sub

    add("run", cmd_run, "keep running, fetching every interval (what the service runs)")
    add("fetch", cmd_fetch, "run one fetch cycle now")
    add("resume", cmd_resume, "forget any back-off and fetch now")
    add("status", cmd_status, "show the last cycle and what each host is waiting for")
    add("list-repos", cmd_list_repos, "print the repositories the config finds")
    install = add(
        "install-resume-agent",
        cmd_install_resume_agent,
        "fetch as soon as the hardware key is plugged in",
    )
    install.add_argument(
        "--print", action="store_true", help="print the launch agent, don't install it"
    )
    add("uninstall-resume-agent", cmd_uninstall_resume_agent, "remove the resume launch agent")
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        return args.func(args, os.environ if env is None else env)
    except (ConfigError, paths.UnsafeDirectoryError, RuntimeError) as error:
        print(f"git-autofetch: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
