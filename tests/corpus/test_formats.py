"""PDF, scanned PDF and DOCX output, and the reproducibility they promise.

Determinism is checked by asserting the *mechanism* -- pinned PDF creation
dates, pinned zip entry timestamps -- rather than by rendering twice with a
sleep in between. Both catch the same bug; only one of them keeps the suite
fast, and the timestamp assertions say which field went wrong when they fail.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pytest

from fleet_copilot.corpus.document import DocumentPlan
from fleet_copilot.corpus.formats import (
    PINNED_TIMESTAMP,
    PRODUCER,
    _latin1,
    render_docx,
    render_pdf,
    render_scanned_pdf,
)


def plan(**overrides: Any) -> DocumentPlan:
    """A small document, in English, safe for a Latin-1 core font."""
    data: dict[str, Any] = {
        "front_matter": {
            "doc_id": "sd-50b-maint-battery-care-rev4",
            "type": "maintenance_procedure",
            "machine_types": ["SD-50B"],
            "item_numbers": ["1.512-340.0"],
            "serials": [],
            "site": None,
            "language": "en",
            "revision": 4,
            "effective_date": "2026-04-12",
        },
        "title": "Battery care",
        "sections": [
            {
                "heading": "Charging",
                "blocks": [
                    {"kind": "paragraph", "text": "Charge in a ventilated area."},
                    {"kind": "steps", "items": ["Park it.", "Connect the charger."]},
                    {"kind": "bullets", "items": ["Never charge above 40 C."]},
                    {"kind": "table", "header": ["Item", "Limit"], "rows": [["Brush", "12 mm"]]},
                ],
            }
        ],
    }
    return DocumentPlan.model_validate(data | overrides)


class TestPdf:
    def test_has_a_text_layer(self) -> None:
        assert b"/Font" in render_pdf(plan())

    def test_contains_the_document_text(self) -> None:
        """A PDF that laid out nothing would still be a valid PDF of a blank page."""
        import zlib

        data = render_pdf(plan())
        streams = ""
        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
            try:
                streams += zlib.decompress(match.group(1)).decode("latin-1")
            except zlib.error:
                continue
        assert "Battery care" in streams
        assert "Charging" in streams

    def test_carries_the_front_matter_facts(self) -> None:
        """A converted document replaces its Markdown, so without this block its
        revision, date and item number never reach the pipeline at all."""
        import zlib

        data = render_pdf(plan())
        streams = ""
        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
            try:
                streams += zlib.decompress(match.group(1)).decode("latin-1")
            except zlib.error:
                continue
        assert "sd-50b-maint-battery-care-rev4" in streams
        assert "2026-04-12" in streams
        assert "1.512-340.0" in streams

    def test_pins_its_creation_date(self) -> None:
        stamp = PINNED_TIMESTAMP.strftime("%Y%m%d%H%M%S").encode()
        assert stamp in render_pdf(plan())

    def test_pins_its_producer(self) -> None:
        assert PRODUCER.encode() in render_pdf(plan())

    def test_is_byte_identical_between_renders(self) -> None:
        assert render_pdf(plan()) == render_pdf(plan())


class TestScannedPdf:
    def test_has_no_text_layer_at_all(self) -> None:
        """The whole point: these words must be reachable only through OCR."""
        data = render_scanned_pdf(plan())
        assert b"/Font" not in data
        assert b" Tj" not in data
        assert b" TJ" not in data

    def test_embeds_a_page_image_with_ink_on_it(self) -> None:
        """A blank sheet is a valid image-only PDF and would pass every check
        above while carrying none of the document."""
        from PIL import Image

        data = render_scanned_pdf(plan())
        jpegs = re.findall(rb"\xff\xd8\xff.*?\xff\xd9", data, re.S)
        assert jpegs
        image = Image.open(io.BytesIO(jpegs[0])).convert("L")
        histogram = image.histogram()
        dark = sum(histogram[:120]) / sum(histogram)
        assert 0.002 < dark < 0.30, dark

    def test_is_byte_identical_between_renders(self) -> None:
        assert render_scanned_pdf(plan()) == render_scanned_pdf(plan())

    def test_pins_its_creation_date(self) -> None:
        stamp = PINNED_TIMESTAMP.strftime("%Y%m%d%H%M%S").encode()
        assert stamp in render_scanned_pdf(plan())


class TestDocx:
    def test_is_a_readable_zip_with_a_document_part(self) -> None:
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        assert "word/document.xml" in archive.namelist()

    def test_contains_the_document_text(self) -> None:
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        body = archive.read("word/document.xml").decode("utf-8")
        assert "Battery care" in body
        assert "Charging" in body
        assert "Connect the charger." in body

    def test_carries_the_front_matter_facts(self) -> None:
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        body = archive.read("word/document.xml").decode("utf-8")
        assert "sd-50b-maint-battery-care-rev4" in body
        assert "2026-04-12" in body

    def test_every_zip_entry_timestamp_is_pinned(self) -> None:
        """python-docx writes the current time into each entry header, which is
        the one thing that made two identical documents differ in bytes."""
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}

    def test_every_zip_entry_records_the_same_creating_platform(self) -> None:
        """zipfile takes this byte from sys.platform -- 0 on Windows, 3 on Unix.

        Unpinned, a DOCX built on a laptop and the same DOCX built in CI differ
        by exactly one byte per entry and by nothing else, so the corpus is
        reproducible on whichever platform generated it and nowhere else. This
        is the bug that got through the first time: every other determinism
        check passed on one machine.
        """
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        assert {info.create_system for info in archive.infolist()} == {0}

    def test_no_zip_header_field_is_left_to_the_platform(self) -> None:
        """Each header field that could vary carries one value across all
        entries, so a future field picking up a platform default shows up as a
        set with two elements rather than as a CI failure nobody can reproduce.
        """
        archive = zipfile.ZipFile(io.BytesIO(render_docx(plan())))
        entries = archive.infolist()
        assert len({info.date_time for info in entries}) == 1
        assert len({info.create_system for info in entries}) == 1
        assert len({info.create_version for info in entries}) == 1
        assert len({info.external_attr for info in entries}) == 1
        assert len({info.compress_type for info in entries}) == 1

    def test_is_byte_identical_between_renders(self) -> None:
        assert render_docx(plan()) == render_docx(plan())

    def test_renders_hungarian(self) -> None:
        """DOCX is how Hungarian reaches a binary format, since PDFs cannot."""
        document = plan(
            title="Akkumulátor gondozása",
            front_matter=plan().front_matter.model_dump() | {"language": "hu"},
            sections=[
                {
                    "heading": "Töltés",
                    "blocks": [{"kind": "paragraph", "text": "Jól szellőző helyen töltse."}],
                }
            ],
        )
        body = zipfile.ZipFile(io.BytesIO(render_docx(document))).read("word/document.xml")
        assert "Akkumulátor gondozása" in body.decode("utf-8")
        assert "szellőző" in body.decode("utf-8")


class TestLatin1Guard:
    def test_maps_typography_the_core_fonts_lack(self) -> None:
        assert _latin1("SD-50B — manual") == "SD-50B - manual"
        assert _latin1("it’s") == "it's"
        assert _latin1("wait…") == "wait..."

    def test_keeps_characters_latin_1_already_has(self) -> None:
        assert _latin1("revision 4 · effective") == "revision 4 · effective"

    def test_refuses_hungarian_rather_than_mangling_it(self) -> None:
        """Silently stripping accents from a corpus used to measure
        cross-language retrieval is worse than failing to build it."""
        with pytest.raises(ValueError, match="cannot be written to a Latin-1"):
            _latin1("Vezetőüléses seprőgép")

    def test_a_hungarian_document_cannot_be_rendered_as_a_pdf(self) -> None:
        document = plan(title="Vezetőüléses seprőgép")
        with pytest.raises(ValueError, match="cannot be written to a Latin-1"):
            render_pdf(document)
