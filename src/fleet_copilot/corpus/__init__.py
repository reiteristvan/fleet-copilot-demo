"""Generate the synthetic document corpus the rest of the pipeline is built on.

The corpus is assembled offline from seed data in ``data/``: a catalogue of
machine types and a spec declaring how many documents of each type and language
to produce. Generation is deterministic, so a committed manifest of content
hashes is a fact that can be re-verified rather than a claim. See
``docs/adr/0003-synthetic-corpus-contract.md``.
"""
