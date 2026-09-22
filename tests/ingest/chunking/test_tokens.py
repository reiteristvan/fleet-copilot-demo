"""Token budgets, and how close the offline heuristic gets to the real thing."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import pytest

from fleet_copilot.corpus.models import Language
from fleet_copilot.ingest.chunking.tokens import CHARS_PER_TOKEN, HeuristicCounter, TokenCounter

FIXTURE = Path(__file__).parent / "fixtures" / "token_counts.json"


def samples() -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["encoding"] == "cl100k_base"
    rows: list[dict[str, Any]] = payload["samples"]
    return rows


def test_hungarian_needs_its_own_ratio() -> None:
    """Measured, not assumed: Hungarian costs roughly twice the tokens.

    cl100k_base was not built for an agglutinative language, so the same
    character budget buys 1.9x the tokens. One global ratio would make every
    Hungarian chunk nearly twice the size the chunker believed it was.
    """
    assert CHARS_PER_TOKEN[Language.EN] > 4.0
    assert CHARS_PER_TOKEN[Language.HU] < 2.5


def test_the_heuristic_tracks_the_real_tokenizer_per_document() -> None:
    """Held to 15%. Boundaries only have to be stable and roughly right.

    A chunker that misjudges a budget by a tenth produces slightly uneven
    chunks; one that misjudges it by half produces chunks that will not embed.
    """
    counter = HeuristicCounter()

    errors = []
    for row in samples():
        language = Language(row["language"])
        estimated = counter.count("x" * int(row["characters"]), language)
        errors.append(abs(estimated - row["tokens"]) / row["tokens"])

    assert statistics.median(errors) < 0.15
    assert max(errors) < 0.40


def test_an_empty_string_costs_nothing() -> None:
    assert HeuristicCounter().count("", Language.EN) == 0


def test_a_short_string_still_costs_at_least_one_token() -> None:
    """Rounding to zero would let a window accept text forever."""
    assert HeuristicCounter().count("a", Language.EN) == 1


def test_budget_to_characters_round_trips() -> None:
    counter = HeuristicCounter()

    characters = counter.characters_for(100, Language.EN)

    assert counter.count("x" * characters, Language.EN) == pytest.approx(100, abs=1)


def test_heuristic_counter_satisfies_the_protocol() -> None:
    assert isinstance(HeuristicCounter(), TokenCounter)
