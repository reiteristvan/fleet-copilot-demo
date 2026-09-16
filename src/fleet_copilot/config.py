"""Runtime configuration, read from the environment.

Values come from the process environment or a local ``.env`` file; ``.env.example``
lists every name. Any field without a default is required, and a missing one
stops the process at startup rather than surfacing as an error on first use.

No endpoint is ever paired with a key: access is granted by role assignment to
the user-assigned managed identity.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        description="libpq connection string for the pgvector-enabled database.",
    )

    azure_openai_endpoint: str | None = None
    azure_openai_chat_deployment: str = "chat"
    azure_openai_embedding_deployment: str = "embeddings"

    # Verified against the deployed account: an unknown version is rejected with
    # 404 rather than ignored, so this is not a value to guess at.
    azure_openai_api_version: str = "2024-10-21"

    azure_search_endpoint: str | None = None
    azure_storage_blob_endpoint: str | None = None
    azure_storage_container: str = "raw-docs"
    azure_key_vault_uri: str | None = None

    # Container Apps sets this to the user-assigned identity and a laptop leaves
    # it unset. Read here only so configuration is inspectable;
    # DefaultAzureCredential picks the same variable up on its own.
    azure_client_id: str | None = None

    healthz_check_azure_openai: bool = False
    healthz_timeout_seconds: float = 5.0

    # The read-only role the copilot connects as. A local development credential
    # in the same class as the container passwords, not a secret: this database
    # runs in a container. A deployed PostgreSQL authenticates this role through
    # Entra ID instead (ADR 0002).
    copilot_ro_password: str = "copilot_ro"
    copilot_ro_user: str = "copilot_ro"


def load_settings() -> Settings:
    """Build :class:`Settings`, converting a validation failure into a clear stop.

    pydantic's ValidationError is precise but arrives as a traceback, which in a
    container is indistinguishable from a crash. This turns it into one message
    naming the variables that are missing.
    """
    try:
        return Settings()
    except Exception as error:
        raise SettingsError(f"invalid configuration:\n{error}") from error
