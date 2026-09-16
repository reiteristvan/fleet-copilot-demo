"""Upload the generated corpus to Blob Storage.

    python scripts/upload_corpus.py            report what would be uploaded
    python scripts/upload_corpus.py --apply    actually upload

Needs the Storage Blob Data Contributor role on your own user principal.
Subscription Owner is a management-plane role and does not grant data-plane
access, so an Owner without it gets a 403 from the first request.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus import upload as corpus_upload
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="upload for real; without it nothing is written to the container",
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
        report = corpus_upload.run(
            manifest,
            corpus_root(args.data_dir),
            settings,
            dry_run=not args.apply,
        )
    except (CorpusDataError, SettingsError) as error:
        print(f"upload failed: {error}", file=sys.stderr)
        return 2

    print(report.describe())
    if report.dry_run:
        print("nothing was written; re-run with --apply to upload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
