"""`cnx`: the ComputeNexa command-line tool (installed with the package).

    cnx whoami
    cnx gpus --app chat
    cnx launch rtx-4090 --app datascience --hours 2 --project titanic
    cnx ls
    cnx ssh cnx-7f3a9c
    cnx stop cnx-7f3a9c

Needs an API key in CNX_API_KEY (dashboard > API keys). Add --json to any command for machine-readable output.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import sys
from datetime import datetime
from typing import Any

from . import __version__
from .client import Client
from .errors import ComputeNexaError
from .models import Project


def money(cents: int | None) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


def when(t: datetime | None) -> str:
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "-"


def table(rows: list[list[Any]], headers: list[str]) -> None:
    cells = [[str(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    print("  ".join(h.upper().ljust(w) for h, w in zip(headers, widths)).rstrip())
    for r in cells:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    if not rows:
        print("(none)")


def as_json(obj: Any) -> Any:
    if isinstance(obj, list):
        return [as_json(o) for o in obj]
    if hasattr(obj, "raw"):
        return obj.raw
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


def out(args: argparse.Namespace, obj: Any, render) -> None:
    if args.json:
        print(json.dumps(as_json(obj), indent=2, default=str))
    else:
        render(obj)


def find_project(cnx: Client, name_or_id: str, create: bool = False) -> Project:
    for p in cnx.list_projects():
        if name_or_id in (p.id, p.name) or p.name.lower() == name_or_id.lower():
            return p
    if create:
        return cnx.create_project(name_or_id)
    raise ComputeNexaError(f"No project named {name_or_id!r}. See `cnx projects`.")


def confirm(args: argparse.Namespace, question: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise ComputeNexaError(f"{question} Add --yes to confirm when not running interactively.")
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise SystemExit("Nothing done.")


# ------------------------------------------------------------------ commands

def cmd_whoami(cnx: Client, args) -> None:
    me = cnx.account()
    out(args, me, lambda m: print(
        f"{m.email} · {m.organization.name} ({m.organization.role}, {m.organization.members} people)\n"
        f"Balance {money(m.balance_cents)} · up to {m.max_concurrent_gpus} GPUs at a time"))


def cmd_gpus(cnx: Client, args) -> None:
    catalog = cnx.catalog(region=args.region)
    gpus = catalog.gpus if args.all else catalog.available(app=args.app)
    out(args, gpus, lambda gs: table(
        [[g.slug, g.name.replace("NVIDIA ", ""), f"{g.vram_gb} GB", money(g.price_per_hour_cents) + "/h"
          + (" (per started hour)" if g.billing_unit == "hour" else ""), "yes" if g.available else "no",
          ",".join(g.regions) or "-"] for g in gs],
        ["gpu", "name", "memory", "price", "available", "regions"]))


def cmd_apps(cnx: Client, args) -> None:
    apps = list(cnx.catalog().apps.values())
    out(args, apps, lambda a: table(
        [[x.name, x.display_name, "required" if x.model_required else ("optional" if x.models else "-"),
          "needed" if x.needs_ssh_key else "-"] for x in a], ["app", "name", "model", "ssh key"]))


def cmd_launch(cnx: Client, args) -> None:
    catalog = cnx.catalog(region=None if args.region == "any" else args.region)
    gpu = catalog.gpu(args.gpu)
    minutes = args.minutes if args.minutes is not None else round(args.hours * 60)
    if gpu.price_per_hour_cents is not None:
        cost = -(-gpu.price_per_hour_cents * args.count * minutes // 60)
        confirm(args, f"Launch {gpu.name} x{args.count} with {args.app} for {minutes} min: about {money(cost)} "
                      "now (unused time refunded)?")
    project = find_project(cnx, args.project, create=args.create_project) if args.project else None
    machine = cnx.launch(gpu.slug, app=args.app, minutes=minutes, gpu_count=args.count, region=args.region,
                         label=args.label, model=args.model, project=project)
    if not args.json:
        print(f"Launched {machine.id}: charged {money(machine.charged_cents)}")
    if args.wait:
        machine.wait_until_running(timeout=args.timeout, on_progress=None if args.json else (
            lambda m: print(f"  {m.state}{': ' + m.startup_progress if m.startup_progress else ''}", flush=True)))
    out(args, machine, lambda m: print(
        f"{m.id} is {m.state}" + (f". Connect: {m.ssh_command}" if m.ssh_command else "")
        + ("" if m.is_running else f". Check: cnx show {m.id}")))


def cmd_ls(cnx: Client, args) -> None:
    machines = cnx.list_instances(active=None if args.all else True, everyone=args.everyone)
    out(args, machines, lambda ms: table(
        [[m.id, m.state, m.gpu, m.app, m.label or "-", when(m.expires_at), money(m.cost_so_far_cents)] for m in ms],
        ["id", "state", "gpu", "app", "label", "ends", "cost so far"]))


def cmd_show(cnx: Client, args) -> None:
    m = cnx.get_instance(args.id)

    def render(m) -> None:
        for k, v in [("id", m.id), ("state", m.state), ("gpu", f"{m.gpu} x{m.gpu_count}"), ("app", m.app),
                     ("label", m.label), ("ssh", m.ssh_command), ("api", m.api_base_url), ("ends", when(m.expires_at)),
                     ("price", money(m.price_per_hour_cents) + "/h"), ("charged", money(m.charged_cents)),
                     ("refunded", money(m.refunded_cents)), ("cost so far", money(m.cost_so_far_cents)),
                     ("progress", m.startup_progress), ("ended", m.end_reason)]:
            if v not in (None, "", "-"):
                print(f"{k:>12}  {v}")
    out(args, m, render)


def cmd_ssh(cnx: Client, args) -> None:
    m = cnx.get_instance(args.id)
    if not m.ssh_command:
        raise ComputeNexaError(f"{m.id} has no SSH command ({m.state}). Add an SSH key (cnx keys add) and wait "
                               "until it's running.")
    command = shlex.split(m.ssh_command) + list(args.ssh_args)
    os.execvp(command[0], command)


def cmd_extend(cnx: Client, args) -> None:
    m = cnx.extend_instance(args.id, minutes=args.minutes, hours=args.hours)
    out(args, m, lambda m: print(f"{m.id} now ends {when(m.expires_at)} (charged {money(m.charged_cents)} in total)"))


def cmd_stop(cnx: Client, args) -> None:
    confirm(args, f"Stop {', '.join(args.ids)}? Files outside a project are deleted.")
    stopped = [cnx.stop_instance(i) for i in args.ids]
    out(args, stopped, lambda ms: [print(f"{m.id}: {m.state}, refunded {money(m.refunded_cents)}") for m in ms])


def cmd_projects(cnx: Client, args) -> None:
    if args.action == "create":
        p = cnx.create_project(args.name)
        out(args, p, lambda p: print(f"Created {p.name} ({p.id})"))
    elif args.action == "rm":
        p = find_project(cnx, args.name)
        confirm(args, f"Delete project {p.name} and all its files?")
        cnx.delete_project(p)
        out(args, {"deleted": p.id}, lambda _: print(f"Deleted {p.name}"))
    else:
        ps = cnx.list_projects()
        out(args, ps, lambda ps: table(
            [[p.name, f"{p.bytes_used / 1e9:.2f} GB", p.files, p.in_use_by or "-", when(p.last_used_at), p.id]
             for p in ps], ["name", "size", "files", "in use by", "last used", "id"]))


def cmd_keys(cnx: Client, args) -> None:
    if args.action == "add":
        path = os.path.expanduser(args.file)
        if not path.endswith(".pub"):
            raise ComputeNexaError("Give the PUBLIC key file (ending in .pub), never the private key.")
        with open(path) as f:
            k = cnx.add_ssh_key(args.name, f.read().strip())
        out(args, k, lambda k: print(f"Added {k.name} ({k.fingerprint})"))
    else:
        ks = cnx.list_ssh_keys()
        out(args, ks, lambda ks: table([[k.name, k.fingerprint, k.id] for k in ks], ["name", "fingerprint", "id"]))


def cmd_usage(cnx: Client, args) -> None:
    u = cnx.usage(days=args.days)
    out(args, u, lambda u: print(
        f"Last {u.days} days: {u.sessions} sessions, {u.gpu_hours:.1f} GPU-hours, spent {money(u.spend_cents)}, "
        f"added {money(u.topups_cents)}. Balance {money(u.balance_cents)}."))


def cmd_receipts(cnx: Client, args) -> None:
    rs = cnx.receipts(days=args.days)
    out(args, rs, lambda rs: table(
        [[r.instance, r.gpu, r.app, when(r.started_at), f"{r.used_minutes:.0f} min", money(r.net_cents), r.outcome]
         for r in rs], ["id", "gpu", "app", "started", "used", "cost", "outcome"]))


def cmd_schedules(cnx: Client, args) -> None:
    if args.action in ("pause", "resume", "rm"):
        s = next((x for x in cnx.list_schedules() if args.name in (x.id, x.name)), None)
        if s is None:
            raise ComputeNexaError(f"No schedule named {args.name!r}. See `cnx schedules`.")
        if args.action == "pause":
            s = cnx.pause_schedule(s, args.until)
            out(args, s, lambda s: print(f"Paused {s.name} until {when(s.paused_until)}"))
        elif args.action == "resume":
            s = cnx.resume_schedule(s)
            out(args, s, lambda s: print(f"Resumed {s.name}; next start {when(s.next_window_start)}"))
        else:
            confirm(args, f"Delete schedule {s.name}?")
            cnx.delete_schedule(s)
            out(args, {"deleted": s.id}, lambda _: print(f"Deleted {s.name}"))
        return
    ss = cnx.list_schedules()
    out(args, ss, lambda ss: table(
        [[s.name, s.app, ",".join(s.gpus), s.timezone, "paused" if s.paused else when(s.next_window_start),
          money(s.estimated_cents_per_month) + "/month"] for s in ss],
        ["name", "app", "gpus", "time zone", "next start", "estimate"]))


# ------------------------------------------------------------------ parser

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cnx", description="ComputeNexa: private GPUs from your terminal. "
                                "Needs CNX_API_KEY (dashboard > API keys).")
    p.add_argument("--version", action="version", version=f"cnx {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def add(name, func, help_):
        s = sub.add_parser(name, parents=[common], help=help_, description=help_)
        s.set_defaults(func=func)
        return s

    def duration(s, required=True):
        g = s.add_mutually_exclusive_group(required=required)
        g.add_argument("--hours", type=float)
        g.add_argument("--minutes", type=int)

    add("whoami", cmd_whoami, "who the API key acts as, your team and balance")
    s = add("gpus", cmd_gpus, "GPUs for sale now with prices, cheapest first")
    s.add_argument("--app", help="only GPUs that run this app")
    s.add_argument("--region", choices=["us", "ca"])
    s.add_argument("--all", action="store_true", help="include GPUs not available right now")
    add("apps", cmd_apps, "the apps a GPU can start with (basic, datascience, chat, imagegen, modelapi, voice)")
    s = add("launch", cmd_launch, "launch a GPU (asks before spending)")
    s.add_argument("gpu", help="a GPU from `cnx gpus`, e.g. rtx-4090")
    s.add_argument("--app", required=True, help="basic | datascience | chat | imagegen | modelapi | voice")
    duration(s)
    s.add_argument("--count", type=int, default=1, help="GPUs in the machine (default 1)")
    s.add_argument("--region", default="any", choices=["any", "us", "ca"])
    s.add_argument("--project", help="project name or id: restored into ~/work and saved back")
    s.add_argument("--create-project", action="store_true", help="create --project if it doesn't exist")
    s.add_argument("--label", help="a name for the machine")
    s.add_argument("--model", help="model for chat, imagegen, modelapi or voice (default: recommended)")
    s.add_argument("--no-wait", dest="wait", action="store_false", help="return without waiting until running")
    s.add_argument("--timeout", type=float, default=900, help="seconds to wait (default 900)")
    s = add("ls", cmd_ls, "your running machines")
    s.add_argument("--all", action="store_true", help="include ended machines")
    s.add_argument("--everyone", action="store_true", help="the whole team's (owners and admins)")
    s = add("show", cmd_show, "one machine's details")
    s.add_argument("id")
    s = add("ssh", cmd_ssh, "connect to a running machine over SSH")
    s.add_argument("id")
    s.add_argument("ssh_args", nargs=argparse.REMAINDER, help="extra ssh arguments or a command to run")
    s = add("extend", cmd_extend, "add time to a running machine")
    s.add_argument("id")
    duration(s)
    s = add("stop", cmd_stop, "stop machines (unused time refunded)")
    s.add_argument("ids", nargs="+", metavar="id")
    s = add("projects", cmd_projects, "list, create or delete projects")
    s.add_argument("action", nargs="?", default="ls", choices=["ls", "create", "rm"])
    s.add_argument("name", nargs="?")
    s = add("keys", cmd_keys, "list or add SSH keys")
    s.add_argument("action", nargs="?", default="ls", choices=["ls", "add"])
    s.add_argument("name", nargs="?")
    s.add_argument("file", nargs="?", default="~/.ssh/id_ed25519.pub", help="public key file")
    s = add("usage", cmd_usage, "GPU use and spending")
    s.add_argument("--days", type=int, default=30)
    s = add("receipts", cmd_receipts, "finished sessions and their cost")
    s.add_argument("--days", type=int, default=30)
    s = add("schedules", cmd_schedules, "list, pause, resume or delete schedules")
    s.add_argument("action", nargs="?", default="ls", choices=["ls", "pause", "resume", "rm"])
    s.add_argument("name", nargs="?", help="schedule name or id")
    s.add_argument("--until", help="pause until this time, ISO 8601 (e.g. 2026-11-02T08:00:00-08:00)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    needs_name = {"projects": ("create", "rm"), "keys": ("add",), "schedules": ("pause", "resume", "rm")}
    if args.command in needs_name and args.action in needs_name[args.command] and not args.name:
        print(f"cnx {args.command} {args.action}: give a name.", file=sys.stderr)
        return 2
    if args.command == "schedules" and args.action == "pause" and not args.until:
        print("cnx schedules pause: give --until.", file=sys.stderr)
        return 2
    try:
        with Client() as cnx:
            args.func(cnx, args)
    except ComputeNexaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
