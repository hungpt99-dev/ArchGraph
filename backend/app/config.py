from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    """Runtime configuration. Everything is environment-driven (12-factor)."""

    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL", "postgresql+psycopg://archgraph:archgraph@localhost:5434/archgraph"
        )
        self.secret_key: str = os.getenv("SECRET_KEY", "dev-insecure-change-me")
        self.encryption_key: str = os.getenv("ENCRYPTION_KEY", "")
        self.access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
        self.repo_storage_dir: str = os.getenv("REPO_STORAGE_DIR", "/data/repos")
        self.workspace_storage_dir: str = os.getenv("WORKSPACE_STORAGE_DIR", "/data/workspaces")
        self.cors_origins: list[str] = [
            o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
        ]
        self.env: str = os.getenv("ARCHGRAPH_ENV", "development")
        self.demo_mode: bool = os.getenv("DEMO_MODE", "false").lower() == "true"
        self.allow_local_repos: bool = os.getenv("ALLOW_LOCAL_REPOS", "true").lower() == "true"
        self.allow_private_networks: bool = (
            os.getenv("ALLOW_PRIVATE_NETWORKS", "false").lower() == "true"
        )
        self.max_scan_file_size: int = int(os.getenv("MAX_SCAN_FILE_SIZE", str(2 * 1024 * 1024)))
        self.scan_timeout_seconds: int = int(os.getenv("SCAN_TIMEOUT_SECONDS", "900"))
        self.github_webhook_secret: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
        self.gitlab_webhook_token: str = os.getenv("GITLAB_WEBHOOK_TOKEN", "")
        self.rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "600"))
        self.job_poll_interval: float = float(os.getenv("JOB_POLL_INTERVAL", "1.0"))
        self.ai_max_context_chars: int = int(os.getenv("AI_MAX_CONTEXT_CHARS", "24000"))
        self.version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
