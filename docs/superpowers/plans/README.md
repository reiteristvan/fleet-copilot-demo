# How plans are written here

A plan is the thinking done before the code, written down so that executing it is
mechanical and so that the reasoning survives the diff. Three have been executed
so far — parsing, chunking, embeddings — and the reasoning in them has been the
most valuable thing in this repository's process. The 220-token window argued
from the corpus's own median rather than from convention, the per-language
characters-per-token ratio, hashing `embed_text` rather than `text`: none of
those would have been arrived at while typing.

## What a plan contains

- **Goal.** One paragraph. What is true when this is finished.
- **Architecture.** The shape, and the alternatives rejected with reasons.
- **Verified facts.** Numbers checked against the live subscription or the corpus,
  with the command that produced them. Not assumptions.
- **Global constraints.** What the gate requires, restated so a task cannot
  quietly weaken it.
- **Tasks in dependency order.** Each with its files, its interfaces, and what it
  must prove.
- **Verification before handing back.** How the author knows it is done.
- **Known gaps, deliberately left.** What is out of scope, and why.

## What a plan does not contain

**Literal test bodies or implementation code.** Describe what each test must
prove; let the implementer write it.

This is not a style preference. Plans 2 and 3 shipped complete, pasteable code,
and five specified tests could not run:

- `@pytest.mark.usefixtures("database")` — no such fixture exists, only
  `database_url`. It appeared in two separate plans.
- `zip(chunks, chunks[1:], strict=True)` for a pairwise walk — always raises,
  because the slice is one shorter, which is the entire point of the idiom.
  `strict=True` had been added to satisfy a lint rule rather than to say
  anything true.
- Fakes annotated `object`, carrying four `# type: ignore` comments to get past
  mypy, where typing them to match the real Protocol removes every one.
- `len(list(records))` after `records` had already been iterated — zero for a
  generator.
- A bare `count(*)` assertion that passes or fails depending on the order the
  suite happened to run in.

Those cost minutes each. The one that cost more was `section_path_at` in plan 2,
where the plan supplied an implementation **and** a test that agreed with it, and
both were wrong: every structural chunk was handed the breadcrumb of the section
above the one its own text was in. The test used offset 0 of a document starting
with its title, where "before any heading" and "at the first heading" are the
same position, so it asserted the off-by-one rather than catching it. A different
module's test found it.

That is the failure this rule exists to prevent. A wrong snippet wastes a debug
cycle. **A wrong specified test steers the implementation toward the wrong
behaviour and then confirms it.**

Code in a plan reads as verified because it is precise, and precision is the one
thing a plan cannot supply — nothing has run it. A behavioural line states the
requirement instead of the mechanism, and cannot agree with a wrong
implementation:

> **Bad** — a body that can be pasted, and can be wrong:
> ```python
> def test_an_offset_before_any_heading_has_an_empty_path() -> None:
>     assert section_path_at(a_document(), 0) == ()
> ```
>
> **Good** — a requirement that cannot be satisfied by the bug:
> - A chunk whose text opens with `## Safety` reports `Safety` as its last
>   section, not the heading above it. Every structural chunk begins at the
>   heading that opened it, so an off-by-one here gives each chunk the breadcrumb
>   of the wrong section.
> - Text before any heading has an empty path. Note that a document starting with
>   its title has no such position, so this needs a fixture with a preamble.

Signatures, types and constants are still worth stating exactly — they are the
contract between tasks, and getting them wrong is caught by the type checker the
moment anything is written. It is executable *behaviour* that must not be
asserted in advance.

## A note on the plans already here

The three executed plans predate this and still contain literal code. They are
kept as written, defects and all, because they are the record of what was
planned rather than a template. The corrections are in the commit bodies and in
`docs/journal.md`.
