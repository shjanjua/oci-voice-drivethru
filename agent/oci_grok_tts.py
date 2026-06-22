from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from urllib.parse import urlencode

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from livekit.agents import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

_DEFAULT_REGION = "us-ashburn-1"
_DEFAULT_VOICE = "eve"
_DEFAULT_LANGUAGE = "en"
_DEFAULT_SAMPLE_RATE = 24000


def _ws_url(*, region: str, voice: str, language: str, sample_rate: int) -> str:
    qs = urlencode(
        {
            "voice": voice,
            "language": language,
            "codec": "pcm",
            "sample_rate": sample_rate,
        }
    )
    return (
        f"wss://inference.generativeai.{region}.oci.oraclecloud.com/xai/v1/tts?{qs}"
    )


class OCIGrokTTS(tts.TTS):
    """LiveKit streaming TTS plugin for OCI Generative AI's xAI Grok TTS.

    Connects to `wss://inference.generativeai.<region>.oci.oraclecloud.com/xai/v1/tts`
    with `Authorization: Bearer <OCI_GENAI_API_KEY>`. Audio chunks arrive as
    raw 16-bit LE PCM as the model generates them.

    Requires an IAM policy authorizing the `generativeaiapikey` principal to
    `use generative-ai-family` in the compartment where the key was minted,
    e.g.:

        allow any-user to use generative-ai-family in compartment <name>
          where ALL {request.principal.type='generativeaiapikey'}
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        region: str = _DEFAULT_REGION,
        voice: str = _DEFAULT_VOICE,
        language: str = _DEFAULT_LANGUAGE,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
    ) -> None:
        key = api_key or os.environ.get("OCI_GENAI_API_KEY")
        if not key:
            raise ValueError(
                "OCI_GENAI_API_KEY is required — mint one in the OCI Console "
                "(Analytics & AI > Generative AI > API Keys)."
            )
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = key
        self._region = region
        self._voice = voice
        self._language = language
        self._sample_rate = sample_rate

    @property
    def model(self) -> str:
        return "xai.grok-tts"

    @property
    def provider(self) -> str:
        return "oracle-oci-genai"

    def _ws_endpoint(self) -> str:
        return _ws_url(
            region=self._region,
            voice=self._voice,
            language=self._language,
            sample_rate=self._sample_rate,
        )

    def _ws_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _OCIGrokChunkedStream(
            tts=self, input_text=text, conn_options=conn_options
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.SynthesizeStream:
        return _OCIGrokSynthesizeStream(tts=self, conn_options=conn_options)


async def _open_ws(grok: OCIGrokTTS, timeout: float):
    try:
        return await asyncio.wait_for(
            ws_connect(
                grok._ws_endpoint(), additional_headers=grok._ws_headers()
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError as e:
        raise APITimeoutError(message="OCI Grok TTS connect timeout") from e
    except InvalidStatus as e:
        raise APIStatusError(
            message=f"OCI Grok TTS WS rejected: {e}",
            status_code=getattr(getattr(e, "response", None), "status_code", 500),
        ) from e
    except Exception as e:
        raise APIConnectionError(f"OCI Grok TTS WS connect failed: {e}") from e


class _OCIGrokChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        grok: OCIGrokTTS = self._tts  # type: ignore[assignment]
        text = self._input_text.strip()

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=grok._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )
        if not text:
            output_emitter.flush()
            return

        ws = await _open_ws(grok, self._conn_options.timeout)
        try:
            await ws.send(json.dumps({"type": "text.delta", "delta": text}))
            await ws.send(json.dumps({"type": "text.done"}))
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "audio.delta":
                    output_emitter.push(base64.b64decode(msg["delta"]))
                elif t == "audio.done":
                    break
                elif t == "error":
                    raise APIStatusError(
                        message=f"OCI Grok TTS error: {msg.get('message')}",
                        status_code=500,
                    )
        except ConnectionClosed as e:
            raise APIConnectionError(f"OCI Grok TTS WS closed: {e}") from e
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
        output_emitter.flush()


class _OCIGrokSynthesizeStream(tts.SynthesizeStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        grok: OCIGrokTTS = self._tts  # type: ignore[assignment]

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=grok._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )

        ws = await _open_ws(grok, self._conn_options.timeout)
        # Segments are strictly sequential on this WS: must wait for audio.done
        # before sending the next segment's text.delta.
        segment_done = asyncio.Event()
        segment_done.set()
        in_segment = False
        fatal: list[BaseException] = []

        async def send_task() -> None:
            nonlocal in_segment
            try:
                async for data in self._input_ch:
                    if isinstance(data, self._FlushSentinel):
                        if in_segment:
                            await ws.send(json.dumps({"type": "text.done"}))
                            in_segment = False
                            await segment_done.wait()
                        continue
                    if not data or not data.strip():
                        continue
                    if not in_segment:
                        segment_done.clear()
                        output_emitter.start_segment(
                            segment_id=utils.shortuuid()
                        )
                        in_segment = True
                    await ws.send(
                        json.dumps({"type": "text.delta", "delta": data})
                    )
                if in_segment:
                    await ws.send(json.dumps({"type": "text.done"}))
                    in_segment = False
                    await segment_done.wait()
            except ConnectionClosed as e:
                fatal.append(APIConnectionError(f"OCI Grok TTS WS closed: {e}"))

        async def recv_task() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "audio.delta":
                        output_emitter.push(base64.b64decode(msg["delta"]))
                    elif t == "audio.done":
                        output_emitter.end_segment()
                        segment_done.set()
                    elif t == "error":
                        fatal.append(
                            APIStatusError(
                                message=f"OCI Grok TTS error: {msg.get('message')}",
                                status_code=500,
                            )
                        )
                        segment_done.set()
                        return
            except ConnectionClosed as e:
                if not fatal:
                    fatal.append(
                        APIConnectionError(f"OCI Grok TTS WS closed: {e}")
                    )
                segment_done.set()

        sender = asyncio.create_task(send_task(), name="oci-grok-tts-send")
        receiver = asyncio.create_task(recv_task(), name="oci-grok-tts-recv")

        try:
            await sender
            with contextlib.suppress(Exception):
                await ws.close()
            await receiver
        except asyncio.CancelledError:
            sender.cancel()
            receiver.cancel()
            with contextlib.suppress(Exception):
                await ws.close()
            with contextlib.suppress(BaseException):
                await asyncio.gather(sender, receiver, return_exceptions=True)
            raise
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
            for t in (sender, receiver):
                if not t.done():
                    t.cancel()

        if fatal:
            raise fatal[0]
