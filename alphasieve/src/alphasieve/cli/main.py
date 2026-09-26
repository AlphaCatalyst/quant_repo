import argparse
import json
import sys

from alphasieve import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alphasieve")
    sub = parser.add_subparsers(dest="command", required=True)

    version = sub.add_parser("version")
    version.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "version":
        if args.json:
            print(json.dumps({"status": "ok", "version": __version__}))
        else:
            print(__version__)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
