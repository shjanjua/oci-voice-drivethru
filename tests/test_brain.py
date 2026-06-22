"""Offline consistency checks for the OCI cascade brain's language wiring.

Does NOT construct a brain (that would load Silero/turn-detector models and read ~/.oci/config) —
it pins the static maps so a language can never be offered without an STT + TTS code behind it.
"""
from agent import brain
from agent.menu import LANGUAGES


def test_every_supported_language_has_stt_and_tts_codes():
    for code in LANGUAGES:
        assert code in brain._OCI_SPEECH_LANG, f"{code} missing an OCI Speech language code"
        assert code in brain._GROK_LANG, f"{code} missing an OCI Grok TTS language code"


def test_oci_openai_base_url():
    assert brain._oci_openai_base("us-ashburn-1") == \
        "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/openai/v1"
