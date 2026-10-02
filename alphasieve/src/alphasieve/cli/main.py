import argparse
import json
import os
import sys
import traceback

from alphasieve.audit import record_event
from alphasieve.cli import (  # noqa: F401  (register commands)
    commands_campaign,
    commands_core,
    commands_data,
    commands_factor,
    commands_fresh,
    commands_service,
    commands_strategy,
    commands_train,
    commands_web,
)
from alphasieve.cli.registry import COMMANDS, Context, envelope
from alphasieve.config import get_settings
from alphasieve.errors import AlphaSieveError, permission_denied
from alphasieve.util import pretty_json


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    parser = argparse.ArgumentParser(prog="alphasieve")
    groups: dict[str, argparse._SubParsersAction] = {}
    top = parser.add_subparsers(dest="command_path", required=True)
    for name, cmd in sorted(COMMANDS.items()):
        parts = name.split(" ")
        if len(parts) == 1:
            sub = top.add_parser(parts[0], parents=[common], help=cmd.help)
        else:
            group_name, leaf = parts
            if group_name not in groups:
                group_parser = top.add_parser(group_name)
                groups[group_name] = group_parser.add_subparsers(dest="leaf", required=True)
            sub = groups[group_name].add_parser(leaf, parents=[common], help=cmd.help)
        sub.set_defaults(_command=name)
        if cmd.configure:
            cmd.configure(sub)
    return parser


def _audit_args(args: argparse.Namespace) -> dict:
    return {k: v for k, v in vars(args).items() if not k.startswith("_") and k not in ("json", "command_path", "leaf")}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    name = args._command
    cmd = COMMANDS[name]
    result, error, ctx = None, None, None
    try:
        settings = get_settings()
        ctx = Context(settings, cmd)
        if settings.role not in cmd.roles:
            raise permission_denied(f"role {settings.role} may not run '{name}'", allowed=list(cmd.roles))
        result = cmd.handler(args, ctx)
    except AlphaSieveError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001
        error = AlphaSieveError("INTERNAL", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()})
    out = envelope(name, result, error)
    if ctx is not None and cmd.needs_state:
        try:
            record_event(
                ctx.conn,
                ctx.settings,
                "command",
                status=out["status"] if out["error"] is None else out["error"]["code"],
                command=name,
                payload={"args": _audit_args(args), "turn": os.environ.get("ALPHASIEVE_TURN")},
            )
        except AlphaSieveError:
            pass
        finally:
            ctx.close()
    if ctx is not None and error is None:
        try:
            from alphasieve import tracking

            run_id = tracking.record(name, _audit_args(args), out, ctx.settings)
            if run_id:
                out["warnings"] = [*out["warnings"], f"tracked on RunLab as run {run_id}"]
        except Exception as exc:  # noqa: BLE001
            out["warnings"] = [*out["warnings"], f"RunLab tracking failed: {type(exc).__name__}: {exc}"]
    if getattr(args, "json", False):
        print(json.dumps(out, ensure_ascii=False, default=str))
    else:
        print(pretty_json(out["data"] if out["error"] is None else out))
    final_error = error or (result.error if result else None)
    return final_error.exit_code if final_error else 0


if __name__ == "__main__":
    sys.exit(main())
