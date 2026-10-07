"""Runtime configuration. Everything comes from environment variables (BUILDBIX_*)."""
import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("BUILDBIX_DATA_DIR", "./data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_url = os.getenv("BUILDBIX_DATABASE_URL", f"sqlite:///{self.data_dir / 'buildbix.db'}")
        self.secret_key = os.getenv("BUILDBIX_SECRET_KEY") or self._dev_key()
        self.token_ttl_minutes = int(os.getenv("BUILDBIX_TOKEN_TTL_MINUTES", "60"))
        self.max_upload_bytes = int(os.getenv("BUILDBIX_MAX_UPLOAD_MB", "100")) * 1024 * 1024
        self.max_extracted_bytes = int(os.getenv("BUILDBIX_MAX_EXTRACTED_MB", "500")) * 1024 * 1024
        self.max_zip_files = int(os.getenv("BUILDBIX_MAX_ZIP_FILES", "20000"))
        self.build_timeout_seconds = int(os.getenv("BUILDBIX_BUILD_TIMEOUT", "3600"))
        self.build_memory = os.getenv("BUILDBIX_BUILD_MEMORY", "6g")
        self.build_cpus = os.getenv("BUILDBIX_BUILD_CPUS", "2")
        self.sandbox_network = os.getenv("BUILDBIX_SANDBOX_NETWORK", "")  # docker network, e.g. one behind an egress proxy
        self.sandbox_proxy = os.getenv("BUILDBIX_SANDBOX_PROXY", "")  # http(s) proxy URL passed into build containers
        self.ai_api_key = os.getenv("BUILDBIX_AI_API_KEY", "")
        self.ai_model = os.getenv("BUILDBIX_AI_MODEL", "claude-sonnet-5-5")

    def _dev_key(self) -> str:
        """Dev-only fallback: generate once and persist. Production MUST set BUILDBIX_SECRET_KEY."""
        path = self.data_dir / ".secret_key"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
        return path.read_text().strip()


settings = Settings()
