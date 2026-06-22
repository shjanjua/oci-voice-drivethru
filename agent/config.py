"""Central config — all tunables env-driven (pydantic-settings).

Env vars are matched case-insensitively to field names, so OCI_GENAI_API_KEY ->
oci_genai_api_key. Copy .env.example to .env and fill it in.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- language ----
    multilingual: bool = True
    default_language: str = "en"

    # ---- OCI Generative AI: LLM + xAI Grok TTS (bearer key) ----
    oci_genai_api_key: str = ""
    oci_genai_region: str = "us-ashburn-1"
    oci_genai_llm_model: str = "openai.gpt-oss-20b"
    oci_grok_voice: str = "eve"

    # ---- OCI Speech: STT (auth via ~/.oci/config IAM signer) ----
    # Compartment for the Speech realtime session; empty => the tenancy OCID from ~/.oci/config.
    oci_compartment_id: str = ""

    # ---- pipeline turn-taking ----
    pipeline_min_words: int = 2

    # ---- walk-off auto-reset (push-to-talk booth) ----
    # The mic is muted until the customer holds PTT, so the SDK user-away detector (default 15s)
    # fires purely on a wall-clock while the customer reads the menu in silence — the supervisor's
    # user_state_changed handler then resets the booth (re-blurs to idle) mid-read. Keep this well
    # past a menu-reading pause; set USER_AWAY_TIMEOUT= (empty/none/off) to DISABLE away-detection.
    user_away_timeout: float | None = 60.0

    # ---- per-tab room teardown (multi-user isolation) ----
    # Seconds the agent waits after the kiosk participant leaves before deleting its room (which ends
    # the job, freeing the subprocess). Must comfortably exceed a page-reload / network blip so a
    # reload rejoins the SAME room instead of being torn down. <=0 tears down immediately (no reload
    # grace — not recommended).
    kiosk_teardown_grace: float = 40.0

    @field_validator("user_away_timeout", mode="before")
    @classmethod
    def _parse_user_away_timeout(cls, v):
        """USER_AWAY_TIMEOUT='' | 'none' | 'null' | 'disabled' | 'off' -> None (disable away-detection).
        A bare float|None field raises float_parsing on these env strings; numeric strings parse
        normally; an UNSET env var falls through to the default."""
        if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "disabled", "off"):
            return None
        return v

    # ---- menu ----
    out_of_stock: str = "cortado"

    # ---- membership: Oracle ADB (optional — guest mode if unset) ----
    db_user: str = "voicedt"
    db_password: str = ""
    db_dsn: str = "sjall_high"
    wallet_dir: str = ""
    wallet_password: str = ""

    # ---- web backend / LiveKit (self-hosted) ----
    livekit_url: str = "ws://localhost:7880"          # agent worker -> local server
    livekit_public_url: str = ""                       # browser -> public wss (via nginx); falls back to livekit_url
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"   # matches `livekit-server --dev` built-in creds


settings = Settings()
