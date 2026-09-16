"""Generate the synthetic document corpus, or check the committed one.

    python scripts/gen_corpus.py            write data/corpus/ and data/manifest.json
    python scripts/gen_corpus.py --check    report any drift, write nothing

Deliberately thin: mypy covers src and tests, not scripts, so everything with a
decision in it lives in fleet_copilot.corpus.build.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from fleet_copilot.corpus import build as corpus_build
from fleet_copilot.corpus.manifest import Manifest
from fleet_copilot.corpus.seed import CorpusDataError


def summarise(manifest: Manifest) -> str:
    """Describe the corpus in a few ASCII lines.

    ASCII on purpose: a Windows console defaults to cp1252 and raises
    UnicodeEncodeError on the Hungarian in a document title, which would turn a
    successful generation into a crash at the last step.
    """
    formats = Counter(entry.format.value for entry in manifest.documents)
    languages = Counter(entry.language.value for entry in manifest.documents)
    lines = [
        f"{manifest.total} documents (seed {manifest.seed})",
        "  formats:   " + ", ".join(f"{name} {count}" for name, count in sorted(formats.items())),
        "  languages: " + ", ".join(f"{name} {count}" for name, count in sorted(languages.items())),
        f"  planted:   {len(manifest.planted())} documents, {len(manifest.planted_ids())} cases",
    ]
    lines.extend(
        f"    {entry.planted_id}: {entry.doc_id} [{entry.format.value}]"
        for entry in sorted(manifest.planted(), key=lambda e: (e.planted_id or "", e.doc_id))
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed corpus and manifest without writing anything",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="seed data directory (default: the repository's data/)",
    )
    args = parser.parse_args()

    try:
        if args.check:
            problems = corpus_build.check(args.data_dir)
            if problems:
                print(f"corpus check failed ({len(problems)} problems):", file=sys.stderr)
                for problem in problems:
                    print(f"  {problem}", file=sys.stderr)
                return 1
            print("corpus matches the committed manifest")
            return 0

        manifest = corpus_build.build(args.data_dir)
    except CorpusDataError as error:
        print(f"corpus generation failed: {error}", file=sys.stderr)
        return 2

    print(summarise(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
