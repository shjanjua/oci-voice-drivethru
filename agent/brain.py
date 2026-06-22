"""The barista Agent + make_brain — the single, OCI-only cascade brain.

One brain, fully Oracle: STT = OCI Speech, LLM = OCI Generative AI (gpt-oss), TTS = OCI
Generative AI xAI Grok. Turn-taking is owned by LiveKit (Silero VAD + the turn-detector,
with word-gated interruption so a cough/single word can't barge in).

`make_brain` is language-parameterised: the STT language code and the TTS language are set per
brain, so a runtime language switch is just `update_agent(make_brain(lang=...))` (see
SessionSupervisor.set_language). The LLM is language-agnostic and follows the system prompt.
"""
from __future__ import annotations

from livekit.agents import Agent, TurnHandlingOptions
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.english import EnglishModel
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from .config import Settings
from .menu import build_system_prompt
from .oci_grok_tts import OCIGrokTTS
from .oci_stt import OCISpeechSTT
from .tools import ALL_TOOLS

# App language code -> OCI Speech (realtime) BCP-47 language code.
_OCI_SPEECH_LANG = {
    "en": "en-US", "es": "es-ES", "fr": "fr-FR", "hi": "hi-IN", "de": "de-DE",
}
# App language code -> OCI Grok TTS language code.
_GROK_LANG = {
    "en": "en", "es": "es", "fr": "fr", "hi": "hi", "de": "de",
}


def _oci_openai_base(region: str) -> str:
    return f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1"


def build_stt(s: Settings, lang: str = "en") -> OCISpeechSTT:
    """OCI Speech realtime STT (auth via ~/.oci/config IAM signer)."""
    return OCISpeechSTT(
        compartment_id=(s.oci_compartment_id or None),
        model_type="ORACLE",
        language=_OCI_SPEECH_LANG.get(lang, "en-US"),
    )


def build_llm(s: Settings) -> openai.LLM:
    """OCI Generative AI LLM via the OpenAI-compatible endpoint (bearer key)."""
    return openai.LLM(
        model=s.oci_genai_llm_model,
        base_url=_oci_openai_base(s.oci_genai_region),
        api_key=s.oci_genai_api_key,
        reasoning_effort="low",
    )


def build_tts(s: Settings, lang: str = "en") -> OCIGrokTTS:
    """OCI Generative AI xAI Grok TTS (bearer key)."""
    return OCIGrokTTS(
        api_key=s.oci_genai_api_key,
        region=s.oci_genai_region,
        voice=s.oci_grok_voice,
        language=_GROK_LANG.get(lang, "en"),
    )


# VAD + turn-detector are heavyweight models; load each once and reuse across brain swaps
# (resets, language switches) so a swap never re-prewarms the ONNX models.
_vad = None
_turn_detectors: dict[bool, object] = {}


def _get_vad():
    global _vad
    if _vad is None:
        _vad = silero.VAD.load()
    return _vad


def _get_turn_detector(multilingual: bool):
    if multilingual not in _turn_detectors:
        _turn_detectors[multilingual] = MultilingualModel() if multilingual else EnglishModel()
    return _turn_detectors[multilingual]


def _turn_handling(turn_detector, *, min_words: int = 2) -> TurnHandlingOptions:
    """LiveKit owns turns; word-gating rejects cough/single-word barge-ins."""
    return TurnHandlingOptions(
        turn_detection=turn_detector,
        endpointing={"mode": "fixed", "min_delay": 0.4, "max_delay": 3.0},
        interruption={"mode": "adaptive", "min_duration": 0.5, "min_words": min_words},
    )


class BaristaAgent(Agent):
    def __init__(self, *, out_of_stock: str, default_language: str = "en", chat_ctx=None,
                 context_note: str = "", **inference) -> None:
        instructions = build_system_prompt(out_of_stock, default_language)
        if context_note:
            instructions += "\n\n" + context_note
        super().__init__(
            instructions=instructions,
            tools=ALL_TOOLS,
            chat_ctx=chat_ctx,
            **inference,   # stt / llm / tts / vad / turn_handling
        )


def make_brain(s: Settings, *, lang: str | None = None, chat_ctx=None,
               order_summary: str | None = None) -> BaristaAgent:
    """Build the one cascade brain for `lang`. Pass order_summary on a mid-order swap (e.g. a
    language switch) so the new brain continues the order instead of re-greeting."""
    lang = lang or s.default_language
    note = ""
    if order_summary:
        note = (f"(An order is already in progress: {order_summary}. Continue from here — "
                f"do NOT start over or re-greet.)")
    return BaristaAgent(
        out_of_stock=s.out_of_stock,
        default_language=lang,
        chat_ctx=chat_ctx,
        context_note=note,
        stt=build_stt(s, lang),
        llm=build_llm(s),
        tts=build_tts(s, lang),
        vad=_get_vad(),
        turn_handling=_turn_handling(_get_turn_detector(s.multilingual), min_words=s.pipeline_min_words),
    )
