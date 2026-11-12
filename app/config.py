"""Settings from the environment (PRD 4.5) and the sandbox-only guard (INV-12)."""
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_HOST = "api-m.sandbox.paypal.com"
PAYPAL_MODES = ("sandbox", "fake")
LLM_MODES = ("live", "fake")


class ConfigError(RuntimeError):
    """Raised at startup when the configuration breaks an invariant."""


def load_env_file(path: Path | None = None) -> None:
    """Reads KEY=VALUE lines from .env into os.environ. Real environment variables win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    paypal_mode: str
    paypal_base_url: str
    paypal_client_id: str
    paypal_client_secret: str
    demo_payee_email_correct: str
    demo_payee_email_typo: str
    llm_mode: str
    llm_api_key: str
    llm_model: str
    llm_daily_call_cap: int
    investigator_max_steps: int
    db_path: str
    sync_loop_enabled: bool
    sync_interval_seconds: int
    unclaimed_grace_seconds: int

    def validate(self) -> None:
        """Startup guard. Raises ConfigError, so the app never serves with a bad setup."""
        host = urlparse(self.paypal_base_url).hostname
        if host != SANDBOX_HOST:
            raise ConfigError(f"PAYPAL_BASE_URL host must be {SANDBOX_HOST}, got {host!r} (INV-12)")
        if self.paypal_mode not in PAYPAL_MODES:
            raise ConfigError(f"PAYPAL_MODE must be one of {PAYPAL_MODES}, got {self.paypal_mode!r}")
        if self.llm_mode not in LLM_MODES:
            raise ConfigError(f"LLM_MODE must be one of {LLM_MODES}, got {self.llm_mode!r}")


def get_settings() -> Settings:
    """Reads the environment on every call, so tests can change it. Blank values use the defaults."""
    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, "").strip() or default

    return Settings(
        paypal_mode=env("PAYPAL_MODE", "sandbox"),
        paypal_base_url=env("PAYPAL_BASE_URL", f"https://{SANDBOX_HOST}"),
        paypal_client_id=env("PAYPAL_CLIENT_ID"),
        paypal_client_secret=env("PAYPAL_CLIENT_SECRET"),
        demo_payee_email_correct=env("DEMO_PAYEE_EMAIL_CORRECT"),
        demo_payee_email_typo=env("DEMO_PAYEE_EMAIL_TYPO"),
        llm_mode=env("LLM_MODE", "live"),
        llm_api_key=env("LLM_API_KEY"),
        llm_model=env("LLM_MODEL"),
        llm_daily_call_cap=int(env("LLM_DAILY_CALL_CAP", "300")),
        investigator_max_steps=int(env("INVESTIGATOR_MAX_STEPS", "6")),
        db_path=env("DB_PATH", "./data/synapsepay.db"),
        sync_loop_enabled=_bool(env("SYNC_LOOP_ENABLED", "true")),
        sync_interval_seconds=int(env("SYNC_INTERVAL_SECONDS", "5")),
        unclaimed_grace_seconds=int(env("UNCLAIMED_GRACE_SECONDS", "600")),
    )
