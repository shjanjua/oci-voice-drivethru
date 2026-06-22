from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Literal

import oci
from oci.ai_speech.models import RealtimeParameters
from oci.config import from_file
from oci.signer import Signer
from oci_ai_speech_realtime import (
    RealtimeSpeechClient,
    RealtimeSpeechClientListener,
)

from livekit.agents import APIConnectionError, APIStatusError, stt, utils
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given

ModelType = Literal["ORACLE", "WHISPER"]
ModelDomain = Literal["GENERIC", "MEDICAL"]


class OCISpeechSTT(stt.STT):
    """LiveKit STT plugin backed by Oracle OCI Speech (Realtime).

    Auth is sourced from `~/.oci/config` using the given profile (DEFAULT).
    The `compartment_id` is read from the `OCI_COMPARTMENT_ID` env var, falling
    back to the tenancy OCID (root compartment) from the OCI config.
    """

    def __init__(
        self,
        *,
        compartment_id: str | None = None,
        region: str | None = None,
        oci_profile: str = "DEFAULT",
        oci_config_path: str = "~/.oci/config",
        model_type: ModelType = "ORACLE",
        model_domain: ModelDomain = "GENERIC",
        language: str = "en-US",
        sample_rate: int = 16000,
        stabilize_partial_results: str = RealtimeParameters.STABILIZE_PARTIAL_RESULTS_LOW,
        partial_silence_threshold_ms: int = 0,
        final_silence_threshold_ms: int = 1000,
        punctuation: str = RealtimeParameters.PUNCTUATION_AUTO,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=(model_type == "ORACLE"),
                offline_recognize=False,
            ),
        )

        if sample_rate not in (8000, 16000):
            raise ValueError("OCI Speech accepts sample_rate of 8000 or 16000")

        self._oci_config = from_file(oci_config_path, oci_profile)
        self._signer = Signer(
            tenancy=self._oci_config["tenancy"],
            user=self._oci_config["user"],
            fingerprint=self._oci_config["fingerprint"],
            private_key_file_location=self._oci_config["key_file"],
            pass_phrase=self._oci_config.get("pass_phrase"),
        )

        self._compartment_id = (
            compartment_id
            or os.getenv("OCI_COMPARTMENT_ID")
            or self._oci_config["tenancy"]
        )
        endpoint_region = region or self._oci_config["region"]
        self._endpoint = (
            f"wss://realtime.aiservice.{endpoint_region}.oci.oraclecloud.com"
        )

        self._model_type: ModelType = model_type
        self._model_domain: ModelDomain = model_domain
        self._language = language
        self._sample_rate = sample_rate
        self._stabilize_partial_results = stabilize_partial_results
        self._partial_silence_threshold_ms = partial_silence_threshold_ms
        self._final_silence_threshold_ms = final_silence_threshold_ms
        self._punctuation = punctuation

    @property
    def model(self) -> str:
        return f"oci-{self._model_type.lower()}"

    @property
    def provider(self) -> str:
        return "oracle-oci-speech"

    def _build_params(self, language: str) -> RealtimeParameters:
        p = RealtimeParameters()
        p.encoding = f"audio/raw;rate={self._sample_rate}"
        p.language_code = language
        p.model_domain = self._model_domain
        p.model_type = self._model_type
        p.is_ack_enabled = False
        p.punctuation = self._punctuation

        if self._model_type == "ORACLE":
            p.partial_silence_threshold_in_ms = self._partial_silence_threshold_ms
            p.final_silence_threshold_in_ms = self._final_silence_threshold_ms
            p.stabilize_partial_results = self._stabilize_partial_results
            p.should_ignore_invalid_customizations = False
            p.customizations = []
        else:
            # WHISPER rejects these — leave None.
            p.partial_silence_threshold_in_ms = None
            p.final_silence_threshold_in_ms = None
            p.stabilize_partial_results = None
            p.should_ignore_invalid_customizations = None
            p.customizations = None

        return p

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        raise NotImplementedError(
            "OCISpeechSTT is streaming-only; use .stream() instead of .recognize()"
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> _OCISpeechStream:
        return _OCISpeechStream(
            stt=self,
            conn_options=conn_options,
            language=language if is_given(language) else self._language,
        )


class _OCISpeechStream(stt.RecognizeStream):
    def __init__(
        self,
        *,
        stt: OCISpeechSTT,
        conn_options: APIConnectOptions,
        language: str,
    ) -> None:
        super().__init__(
            stt=stt, conn_options=conn_options, sample_rate=stt._sample_rate
        )
        self._oci_stt = stt
        self._language = language

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        request_id = utils.shortuuid()
        ready_evt = asyncio.Event()
        speaking = [False]
        fatal_err: list[Exception] = []

        sample_rate = self._oci_stt._sample_rate
        chunk_samples = int(sample_rate * 0.096)  # 96 ms

        def push(event_type: stt.SpeechEventType, alts=None) -> None:
            with contextlib.suppress(Exception):
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=event_type,
                        request_id=request_id,
                        alternatives=alts or [],
                    )
                )

        def on_speech_started() -> None:
            if not speaking[0]:
                speaking[0] = True
                push(stt.SpeechEventType.START_OF_SPEECH)

        def on_speech_ended() -> None:
            if speaking[0]:
                speaking[0] = False
                push(stt.SpeechEventType.END_OF_SPEECH)

        oci_stt_ref = self._oci_stt
        language_ref = self._language

        class _Listener(RealtimeSpeechClientListener):
            def on_connect(self) -> None:  # WS upgrade succeeded; wait for CONNECT msg
                pass

            def on_connect_message(self, msg) -> None:  # server accepted creds
                ready_evt.set()

            def on_ack_message(self, msg) -> None:
                pass

            def on_network_event(self, msg) -> None:
                pass

            def on_result(self, msg) -> None:
                transcriptions = msg.get("transcriptions") or []
                if not transcriptions:
                    return
                t = transcriptions[0]
                text = (t.get("transcription") or "").strip()
                if not text:
                    return
                is_final = bool(t.get("isFinal"))
                on_speech_started()
                alts = [
                    stt.SpeechData(
                        language=language_ref,
                        text=text,
                        confidence=1.0,
                    )
                ]
                push(
                    stt.SpeechEventType.FINAL_TRANSCRIPT
                    if is_final
                    else stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    alts=alts,
                )
                if is_final:
                    on_speech_ended()

            def on_error(self, err) -> None:
                code = err.get("code", 500) if isinstance(err, dict) else 500
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                fatal_err.append(
                    APIStatusError(message=f"OCI Speech error: {msg}", status_code=code)
                )

            def on_close(self, code, reason) -> None:
                if code != 1000 and not fatal_err:
                    fatal_err.append(
                        APIConnectionError(
                            f"OCI Speech websocket closed: {code} {reason}"
                        )
                    )

        client = RealtimeSpeechClient(
            config=oci_stt_ref._oci_config,
            realtime_speech_parameters=oci_stt_ref._build_params(language_ref),
            listener=_Listener(),
            service_endpoint=oci_stt_ref._endpoint,
            signer=oci_stt_ref._signer,
            compartment_id=oci_stt_ref._compartment_id,
        )

        async def feeder() -> None:
            await ready_evt.wait()
            self.start_time = loop.time()
            bstream = utils.audio.AudioByteStream(
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=chunk_samples,
            )
            try:
                async for data in self._input_ch:
                    if isinstance(data, self._FlushSentinel):
                        for f in bstream.flush():
                            await client.send_data(f.data.tobytes())
                        if oci_stt_ref._model_type == "ORACLE":
                            with contextlib.suppress(Exception):
                                await client.request_final_result()
                        continue
                    for f in bstream.write(data.data.tobytes()):
                        await client.send_data(f.data.tobytes())
            finally:
                # Drain the audio buffer one last time before closing.
                with contextlib.suppress(Exception):
                    for f in bstream.flush():
                        await client.send_data(f.data.tobytes())
                client.close()

        connect_task = asyncio.create_task(client.connect(), name="oci-stt-connect")
        feed_task = asyncio.create_task(feeder(), name="oci-stt-feed")

        try:
            done, _pending = await asyncio.wait(
                {connect_task, feed_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                if (exc := t.exception()) is not None:
                    raise exc
        finally:
            client.close()
            for t in (connect_task, feed_task):
                if not t.done():
                    t.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(connect_task, feed_task, return_exceptions=True)

        if fatal_err:
            raise fatal_err[0]
