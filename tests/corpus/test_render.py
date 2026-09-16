"""Markdown rendering, and the front matter it publishes."""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml

from fleet_copilot.corpus.document import (
    Block,
    Bullets,
    DocumentPlan,
    Paragraph,
    Section,
    Steps,
    Table,
)
from fleet_copilot.corpus.models import DocumentType, Language
from fleet_copilot.corpus.render import LINE_ENDING, render_front_matter, render_markdown


def plan(**overrides: Any) -> DocumentPlan:
    """A small document to render."""
    front: dict[str, Any] = {
        "doc_id": "sd-50b-maint-battery-care-rev4",
        "type": "maintenance_procedure",
        "machine_types": ["SD-50B"],
        "item_numbers": ["1.512-340.0"],
        "serials": [],
        "site": None,
        "language": "en",
        "revision": 4,
        "effective_date": "2026-04-12",
    }
    data: dict[str, Any] = {
        "front_matter": front | overrides.pop("front_matter", {}),
        "title": "Battery care",
        "sections": [
            {
                "heading": "Charging",
                "blocks": [
                    {"kind": "paragraph", "text": "Charge in a ventilated area."},
                    {"kind": "steps", "items": ["Park it.", "Connect the charger."]},
                    {"kind": "bullets", "items": ["Never above 40 C."]},
                    {
                        "kind": "table",
                        "header": ["Item", "Limit"],
                        "rows": [["Brush", "12 mm"]],
                    },
                ],
            }
        ],
    }
    return DocumentPlan.model_validate(data | overrides)


def parse_front_matter(markdown: str) -> dict[str, Any]:
    """Pull the front-matter block back out of a rendered document."""
    _, _, rest = markdown.partition("---" + LINE_ENDING)
    block, _, _ = rest.partition(LINE_ENDING + "---")
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict)
    return parsed


class TestFrontMatter:
    def test_round_trips_through_a_yaml_parser(self) -> None:
        """Retrieval will parse this block; it has to be real YAML."""
        parsed = parse_front_matter(render_markdown(plan()))
        assert parsed["doc_id"] == "sd-50b-maint-battery-care-rev4"
        assert parsed["type"] == "maintenance_procedure"
        assert parsed["machine_types"] == ["SD-50B"]
        assert parsed["item_numbers"] == ["1.512-340.0"]
        assert parsed["serials"] == []
        assert parsed["site"] is None
        assert parsed["language"] == "en"
        assert parsed["revision"] == 4
        assert parsed["effective_date"] == date(2026, 4, 12)

    def test_publishes_exactly_the_keys_adr_0003_fixes(self) -> None:
        assert set(parse_front_matter(render_markdown(plan()))) == {
            "doc_id",
            "type",
            "machine_types",
            "item_numbers",
            "serials",
            "site",
            "language",
            "revision",
            "effective_date",
        }

    def test_an_item_number_stays_a_string(self) -> None:
        """Unquoted, an item number is only a string by luck of the resolver."""
        parsed = parse_front_matter(render_markdown(plan()))
        assert isinstance(parsed["item_numbers"][0], str)

    def test_key_order_is_fixed(self) -> None:
        """The order is part of the bytes, and the bytes are hashed."""
        rendered = render_front_matter(plan().front_matter)
        keys = [line.split(":")[0] for line in rendered.splitlines()[1:-1]]
        assert keys == [
            "doc_id",
            "type",
            "machine_types",
            "item_numbers",
            "serials",
            "site",
            "language",
            "revision",
            "effective_date",
        ]

    def test_a_site_is_rendered_when_present(self) -> None:
        document = plan(front_matter={"site": "depot-north", "type": "handover_note"})
        assert parse_front_matter(render_markdown(document))["site"] == "depot-north"

    def test_carries_no_planted_marker(self) -> None:
        assert "planted" not in render_markdown(plan())


class TestBody:
    def test_title_is_the_only_h1(self) -> None:
        rendered = render_markdown(plan())
        assert [line for line in rendered.splitlines() if line.startswith("# ")] == [
            "# Battery care"
        ]

    def test_sections_are_h2(self) -> None:
        assert "## Charging" in render_markdown(plan())

    def test_steps_are_numbered_from_one(self) -> None:
        rendered = render_markdown(plan())
        assert "1. Park it." in rendered
        assert "2. Connect the charger." in rendered

    def test_bullets_are_dashed(self) -> None:
        assert "- Never above 40 C." in render_markdown(plan())

    def test_tables_have_a_header_divider(self) -> None:
        rendered = render_markdown(plan())
        assert "| Item | Limit |" in rendered
        assert "| --- | --- |" in rendered
        assert "| Brush | 12 mm |" in rendered

    def test_a_pipe_in_a_cell_does_not_split_the_column(self) -> None:
        """Otherwise the table silently gains a column and the row shifts."""
        document = plan(
            sections=[
                {
                    "heading": "Limits",
                    "blocks": [
                        {
                            "kind": "table",
                            "header": ["Item", "Note"],
                            "rows": [["Brush", "12 mm | measured cold"]],
                        }
                    ],
                }
            ]
        )
        rendered = render_markdown(document)
        assert r"12 mm \| measured cold" in rendered


class TestFileShape:
    def test_uses_line_feeds_only(self) -> None:
        """A CRLF file hashes differently from the same text with LF, so a
        corpus generated on Windows would not match one generated in CI."""
        assert "\r" not in render_markdown(plan())

    def test_ends_with_exactly_one_newline(self) -> None:
        rendered = render_markdown(plan())
        assert rendered.endswith("\n")
        assert not rendered.endswith("\n\n")

    def test_rendering_is_a_pure_function_of_the_plan(self) -> None:
        assert render_markdown(plan()) == render_markdown(plan())

    def test_hungarian_survives_the_round_trip(self) -> None:
        document = plan(
            title="Akkumulátor gondozása",
            front_matter={"language": "hu"},
            sections=[
                {
                    "heading": "Töltés",
                    "blocks": [{"kind": "paragraph", "text": "Jól szellőző helyen töltse."}],
                }
            ],
        )
        rendered = render_markdown(document)
        assert "Akkumulátor gondozása" in rendered
        assert "Töltés" in rendered
        assert "szellőző" in rendered
        assert parse_front_matter(rendered)["language"] == Language.HU.value
        assert document.front_matter.type is DocumentType.MAINTENANCE_PROCEDURE


class TestBlockCoverage:
    def test_every_block_kind_renders(self) -> None:
        """A block kind with no renderer would produce a silently empty section."""
        blocks: list[Block] = [
            Paragraph(text="Text."),
            Bullets(items=("One.",)),
            Steps(items=("First.",)),
            Table(header=("A",), rows=(("1",),)),
        ]
        document = DocumentPlan(
            front_matter=plan().front_matter,
            title="Coverage",
            sections=(Section(heading="All", blocks=tuple(blocks)),),
        )
        rendered = render_markdown(document)
        for expected in ("Text.", "- One.", "1. First.", "| A |", "| 1 |"):
            assert expected in rendered
