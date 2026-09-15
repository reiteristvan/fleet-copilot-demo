"""Guards the "no keys anywhere" rule that the Bicep templates enforce.

The infrastructure disables shared-key and local auth on storage, Azure OpenAI,
Search and Application Insights, so key-based code would fail at runtime rather
than at review. This test moves that failure forward to `just test`.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

KEY_BASED_AUTH = re.compile(
    "|".join(
        [
            r"api[_-]?key",
            r"account[_-]?key",
            r"subscription[_-]?key",
            r"\bAccountKey=",
            r"list_keys|listKeys",
        ]
    ),
    re.IGNORECASE,
)


def test_src_contains_no_key_based_auth() -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{number}: {line.strip()}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if KEY_BASED_AUTH.search(line)
    ]

    assert not offenders, "key-based auth in src/:\n" + "\n".join(offenders)
