"""Initialize and import the local NMPA cosmetics filing database."""

from __future__ import annotations

import argparse
import os

from content_trust.nmpa import NMPARegistry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "import-jsonl", "count"),
        help="init creates the empty schema; import-jsonl accepts normalized records.",
    )
    parser.add_argument("input", nargs="?", help="normalized JSONL file for import-jsonl")
    parser.add_argument("--db", default=os.getenv("NMPA_DB_PATH", "data/nmpa/cosmetics.sqlite3"))
    args = parser.parse_args()
    registry = NMPARegistry(args.db)

    if args.command == "init":
        registry.initialize()
        print(f"Initialized empty NMPA schema at {registry.path}")
    elif args.command == "count":
        print(registry.count())
    else:
        if not args.input:
            parser.error("import-jsonl requires an input path")
        imported = registry.import_jsonl(args.input)
        print(f"Imported or updated {imported} records; database now has {registry.count()} records.")


if __name__ == "__main__":
    main()
