import argparse
import json
import sys
from pathlib import Path

from .client import call, ensure_daemon


def main():
    parser = argparse.ArgumentParser(
        description="LocalForge — user-owned local development services"
    )
    sub = parser.add_subparsers(dest="op", required=True)
    for name in (
        "gui",
        "daemon",
        "status",
        "shutdown",
        "start-all",
        "stop-all",
        "restart-all",
        "reload-all",
        "scan",
        "discover",
        "init-db",
        "sync-hosts",
    ):
        sub.add_parser(name)
    for name in ("start", "stop", "restart", "reload", "check"):
        p = sub.add_parser(name)
        p.add_argument(
            "service", choices=("php", "nginx", "apache", "database", "redis", "node")
        )
    p = sub.add_parser("php")
    p.add_argument("version")
    p = sub.add_parser("tail")
    p.add_argument("name")
    p = sub.add_parser(
        "apply",
        help="Apply a complete JSON settings file; requires all services stopped",
    )
    p.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.op == "gui":
            from .gui import main as gui

            return gui()
        if args.op == "daemon":
            from .daemon import main as daemon

            return daemon()
        if args.op == "shutdown":
            print(call("shutdown"))
            return
        ensure_daemon()
        if args.op == "sync-hosts":
            from .hosts import sync_hosts

            result = sync_hosts(call("snapshot")["settings"]["projects"])
        elif args.op == "status":
            result = call("snapshot")
        elif args.op == "php":
            result = call("switch_php", version=args.version)
        elif args.op == "apply":
            result = call("apply", settings=json.loads(args.file.read_text()))
        elif args.op == "tail":
            result = call("tail", name=args.name)
        elif hasattr(args, "service"):
            result = call(args.op, service=args.service)
        else:
            result = call(args.op.replace("-", "_"))
        print(
            json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result
        )
    except Exception as exc:
        print(f"localforge: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
