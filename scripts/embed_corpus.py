"""Embed the corpus under all three chunking strategies.

    python scripts/embed_corpus.py            report what would be embedded
    python scripts/embed_corpus.py --apply    actually call the model

Needs the local database up (`just up && just db-migrate`), the layout cache
populated (`just corpus-parse --apply`), and Cognitive Services OpenAI User on
your own principal.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest.run import embed_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="embed for real; without it nothing is called"
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(None))
        settings = load_settings()
        reports = asyncio.run(
            embed_corpus(manifest, corpus_root(None), settings, dry_run=not args.apply)
        )
    except (CorpusDataError, SettingsError) as error:
        print(f"embed failed: {error}", file=sys.stderr)
        return 2

    for strategy, report in reports.items():
        print(f"{strategy.value:12} {report.describe()}")
    if any(report.dry_run for report in reports.values()):
        print("nothing was called; re-run with --apply to embed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
