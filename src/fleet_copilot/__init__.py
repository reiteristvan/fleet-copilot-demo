"""Fleet Copilot: an end-to-end retrieval-augmented pipeline over fleet documents.

The package is split along the stages of that pipeline:

``ingest``
    Load source documents and split them into retrievable chunks.
``retrieval``
    Index chunks and fetch the ones relevant to a question.
``agents``
    Plan over and answer from retrieved context.
``evals``
    Score the retrieval and agent layers offline.
``api``
    Entry points that expose the pipeline to the outside world.
"""

from importlib.metadata import version

__version__ = version("fleet-copilot")

__all__ = ["__version__"]
