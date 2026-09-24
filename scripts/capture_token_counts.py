"""Capture exact cl100k_base token counts to test the heuristic against.

Run deliberately, on a machine with network access:

    uv run python scripts/capture_token_counts.py

tiktoken downloads its BPE table over HTTPS on first use. That is why no path
`just check` executes ever reaches it. The counts are committed, so the
heuristic can be held to them offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "corpus" / "markdown"
TARGET = ROOT / "tests" / "ingest" / "chunking" / "fixtures" / "token_counts.json"


def main() -> None:
    encoding = tiktoken.get_encoding("cl100k_base")
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    samples = []
    for path in sorted(CORPUS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[2].lstrip("\n") if text.startswith("---") else text
        samples.append(
            {
                "doc_id": path.stem,
                "language": "hu" if path.stem.endswith("-hu") else "en",
                "characters": len(body),
                "tokens": len(encoding.encode(body)),
            }
        )

    payload = {"encoding": "cl100k_base", "samples": samples}
    TARGET.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{TARGET.name}: {len(samples)} samples")


if __name__ == "__main__":
    raise SystemExit(main())
