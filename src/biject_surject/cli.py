"""Command-line entry point for ``surject``."""

import argparse
import sys
from typing import List, Optional

from biject_surject import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="surject",
        description="Catch when your agent does something you didn't intend.",
    )
    parser.add_argument(
        "--version", action="version", version=f"surject {__version__}"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
