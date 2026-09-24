# How plans are written here

A plan holds the thinking done before the code. Writing it down makes execution
mechanical and keeps the reasoning after the diff lands.

Three plans have been executed: parsing, chunking, embeddings. The reasoning in
them is the most valuable part of this process. The 220-token window came from
the corpus median rather than convention. The characters-per-token ratio is per
language. `content_hash` covers `embed_text` rather than `text`. Nobody reaches
those conclusions while typing.

## What a plan contains

- **Goal.** One paragraph. What is true when this is finished.
- **Architecture.** The shape, and the alternatives rejected with reasons.
- **Verified facts.** Numbers checked against the live subscription or the
  corpus, with the command that produced them. Not assumptions.
- **Global constraints.** What the gate requires, restated so a task cannot
  weaken it quietly.
- **Tasks in dependency order.** Each with its files, its interfaces, and what
  it must prove.
- **Verification before handing back.** How the author knows it is done.
- **Known gaps, deliberately left.** What is out of scope, and why.

## What a plan does not contain

**Literal test bodies or implementation code.** Describe what each test must
prove. Let the implementer write it.

This is not a style preference. Plans 2 and 3 shipped complete, pasteable code.
Five specified tests could not run:

- `@pytest.mark.usefixtures("database")`. No such fixture exists, only
  `database_url`. It appeared in two separate plans.
- `zip(chunks, chunks[1:], strict=True)` for a pairwise walk. It always raises,
  because the slice is one shorter. That is the point of the idiom.
  `strict=True` had been added to satisfy a lint rule.
- Fakes annotated `object`, carrying four `# type: ignore` comments to get past
  mypy. Typing them to match the real Protocol removes every one.
- `len(list(records))` after `records` had already been iterated. Zero for a
  generator.
- A bare `count(*)` assertion that passes or fails depending on the order the
  suite ran in.

Those cost minutes each. `section_path_at` in plan 2 cost more. The plan supplied
an implementation **and** a test that agreed with it, and both were wrong. Every
structural chunk got the breadcrumb of the section above its own text.

The test used offset 0 of a document that starts with its title. At that position
"before any heading" and "at the first heading" are the same thing. So the test
asserted the off-by-one instead of catching it. A different module's test found
the bug.

That is the failure this rule prevents. A wrong snippet wastes a debug cycle.
**A wrong specified test steers the implementation toward the wrong behaviour and
then confirms it.**

Code in a plan reads as verified, because it is precise. Precision is the one
thing a plan cannot supply, since nothing has run it. A behavioural line states
the requirement rather than the mechanism, so it cannot agree with a wrong
implementation:

> **Bad.** A body that can be pasted, and can be wrong:
> ```python
> def test_an_offset_before_any_heading_has_an_empty_path() -> None:
>     assert section_path_at(a_document(), 0) == ()
> ```
>
> **Good.** A requirement the bug cannot satisfy:
> - A chunk whose text opens with `## Safety` reports `Safety` as its last
>   section, not the heading above it. Every structural chunk begins at the
>   heading that opened it, so an off-by-one gives each chunk the breadcrumb of
>   the wrong section.
> - Text before any heading has an empty path. A document that starts with its
>   title has no such position, so this needs a fixture with a preamble.

State signatures, types and constants exactly. They are the contract between
tasks, and the type checker catches a mistake in them as soon as anything is
written. It is executable *behaviour* that must not be asserted in advance.

## A note on the plans already here

The three executed plans predate this rule and still contain literal code. They
stay as written, defects included. They record what was planned. They are not a
template. The corrections are in the commit bodies and in `docs/journal.md`.
