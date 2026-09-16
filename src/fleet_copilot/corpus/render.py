"""Write a planned document out as Markdown with YAML front matter.

The front matter is written by hand rather than through ``yaml.safe_dump``.
The dumper is free to choose quoting and line folding, and those choices change
between releases -- which would change every content hash in the manifest for a
reason that has nothing to do with the corpus. Writing the nine keys directly
costs a few lines and makes the output a function of the document alone.
"""

from __future__ import annotations

from fleet_copilot.corpus.document import (
    Block,
    Bullets,
    DocumentPlan,
    FrontMatter,
    Paragraph,
    Steps,
    Table,
)

LINE_ENDING = "\n"
"""Line ending for every generated file, on every platform.

Not ``os.linesep``. A corpus generated on Windows and one generated in CI must
be byte-identical, and a CRLF file has a different SHA-256 from the same text
with LF.
"""


def _yaml_list(values: tuple[str, ...], *, quote: bool) -> str:
    """Render a front-matter list inline, e.g. ``[SD-50B]`` or ``[]``."""
    if not values:
        return "[]"
    if quote:
        return "[" + ", ".join(f'"{value}"' for value in values) + "]"
    return "[" + ", ".join(values) + "]"


def render_front_matter(front: FrontMatter) -> str:
    """Render the front-matter block, including its delimiters.

    Item numbers and serials are quoted because an unquoted ``1.512-340.0`` is
    only a string by luck of the YAML resolver, and a future item-number format
    that happened to look numeric would silently change type between the file
    and the index.
    """
    lines = [
        "---",
        f"doc_id: {front.doc_id}",
        f"type: {front.type.value}",
        f"machine_types: {_yaml_list(front.machine_types, quote=False)}",
        f"item_numbers: {_yaml_list(front.item_numbers, quote=True)}",
        f"serials: {_yaml_list(front.serials, quote=True)}",
        f"site: {front.site if front.site else 'null'}",
        f"language: {front.language.value}",
        f"revision: {front.revision}",
        f"effective_date: {front.effective_date.isoformat()}",
        "---",
    ]
    return LINE_ENDING.join(lines)


def _escape_cell(text: str) -> str:
    """Escape a table cell so a pipe in the content does not split the column."""
    return text.replace("|", "\\|")


def _render_block(block: Block) -> str:
    """Render one block as Markdown."""
    match block:
        case Paragraph():
            return block.text
        case Bullets():
            return LINE_ENDING.join(f"- {item}" for item in block.items)
        case Steps():
            return LINE_ENDING.join(
                f"{number}. {item}" for number, item in enumerate(block.items, start=1)
            )
        case Table():
            header = "| " + " | ".join(_escape_cell(cell) for cell in block.header) + " |"
            divider = "| " + " | ".join("---" for _ in block.header) + " |"
            rows = [
                "| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in block.rows
            ]
            return LINE_ENDING.join([header, divider, *rows])


def render_markdown(plan: DocumentPlan) -> str:
    """Render ``plan`` as a complete Markdown document, ending in one newline."""
    parts = [render_front_matter(plan.front_matter), "", f"# {plan.title}"]
    for section in plan.sections:
        parts.extend(["", f"## {section.heading}"])
        for block in section.blocks:
            parts.extend(["", _render_block(block)])
    return LINE_ENDING.join(parts) + LINE_ENDING
