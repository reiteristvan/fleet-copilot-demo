"""Report the chunk-size distribution per strategy.

    python scripts/chunk_stats.py                Markdown corpus only, offline
    python scripts/chunk_stats.py --all          all 120, reading the layout cache
    python scripts/chunk_stats.py --exact        report sizes with tiktoken
    python scripts/chunk_stats.py --calibrate    re-measure characters per token

Boundaries are always chosen by the offline heuristic so they are identical on
every machine. --exact only changes how the resulting chunks are *measured*.

Thin on purpose: mypy covers src and tests, not scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys

from fleet_copilot.config import SettingsError, load_settings
from fleet_copilot.corpus.build import corpus_root, manifest_path
from fleet_copilot.corpus.manifest import load_manifest
from fleet_copilot.corpus.seed import CorpusDataError
from fleet_copilot.ingest.chunking.stats import as_markdown_table, summarise
from fleet_copilot.ingest.chunking.tokens import (
    CHARS_PER_TOKEN,
    HeuristicCounter,
    TiktokenCounter,
)
from fleet_copilot.ingest.models import StrategyId
from fleet_copilot.ingest.run import chunk_corpus, chunk_markdown_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include the 25 converted documents")
    parser.add_argument("--exact", action="store_true", help="measure sizes with tiktoken")
    parser.add_argument("--calibrate", action="store_true", help="re-measure characters per token")
    args = parser.parse_args()

    try:
        manifest = load_manifest(manifest_path(None))
        if args.all:
            settings = load_settings()
            by_strategy = asyncio.run(chunk_corpus(manifest, corpus_root(None), settings))
        else:
            by_strategy = asyncio.run(chunk_markdown_corpus(manifest, corpus_root(None)))
    except (CorpusDataError, SettingsError) as error:
        print(f"chunk-stats failed: {error}", file=sys.stderr)
        return 2

    exact = args.exact or args.calibrate
    counter = TiktokenCounter() if exact else HeuristicCounter()
    rows = [summarise(strategy, by_strategy[strategy], counter) for strategy in StrategyId]

    print(f"counter: {'tiktoken cl100k_base' if exact else 'heuristic'}")
    print()
    print(as_markdown_table(rows), end="")

    if args.calibrate:
        # A cross-check, not a regeneration: these are ratios over chunks, while
        # the committed constants are medians over whole documents from
        # scripts/capture_token_counts.py. Different populations, so they should
        # land in the same neighbourhood rather than match. A ratio that has
        # drifted by more than a few percent means re-running that script.
        print()
        print("chunk-level ratios; the constants are per-document (capture_token_counts.py)")
        for language in sorted(
            {chunk.language for chunks in by_strategy.values() for chunk in chunks}
        ):
            texts = [
                chunk.text
                for chunk in by_strategy[StrategyId.STRUCTURAL]
                if chunk.language is language
            ]
            ratios = [len(text) / counter.count(text, language) for text in texts]
            aggregate = sum(len(text) for text in texts) / sum(
                counter.count(text, language) for text in texts
            )
            print(
                f"chars per token, {language.value}: "
                f"median {statistics.median(ratios):.3f}, aggregate {aggregate:.3f} "
                f"(constant {CHARS_PER_TOKEN[language]})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
