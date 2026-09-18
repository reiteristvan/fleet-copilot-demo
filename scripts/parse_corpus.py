"""Parse the corpus: Markdown natively, PDF and DOCX through the service.

    python scripts/parse_corpus.py            report what would be analysed
    python scripts/parse_corpus.py --apply    actually call the service

Needs the Cognitive Services User role on the Document Intelligence account and
Storage Blob Data Contributor on the storage account, both on your own user
principal. Subscription Owner is a management-plane role and does not grant
data-plane access, so an Owner without them gets a 403 from the first request.
`infra/params/dev.bicepparam` grants both through developerPrincipalId.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest import run as ingest_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="analyse for real; without it the service is never called",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="seed data directory (default: the repository's data/)",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(args.data_dir))
        settings = load_settings()
        report = asyncio.run(
            ingest_run.run(
                manifest,
                corpus_root(args.data_dir),
                settings,
                dry_run=not args.apply,
            )
        )
    except (CorpusDataError, SettingsError) as error:
        print(f"parse failed: {error}", file=sys.stderr)
        return 2

    print(report.describe())
    if report.dry_run:
        print("the service was not called; re-run with --apply to analyse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
