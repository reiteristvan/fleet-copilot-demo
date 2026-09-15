"""Runtime configuration, read from the environment.

``infra/deploy.sh`` prints these as ``export`` lines after a deployment;
Container Apps supplies the same names as container environment variables. No
endpoint is ever paired with a key: access is granted by role assignment to the
user-assigned identity.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AzureSettings(BaseSettings):
    """Endpoints of the deployed infrastructure."""

    model_config = SettingsConfigDict(env_prefix="AZURE_", extra="ignore")

    openai_endpoint: str
    openai_chat_deployment: str = "chat"
    openai_embedding_deployment: str = "embeddings"
    search_endpoint: str
    storage_blob_endpoint: str
    storage_container: str = "raw-docs"
    key_vault_uri: str | None = None

    # Set by Container Apps to the user-assigned identity, unset locally. It is
    # read here only so that configuration is inspectable; DefaultAzureCredential
    # picks the same variable up on its own.
    client_id: str | None = None
