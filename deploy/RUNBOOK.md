# Voice Drive-Thru — Operator Runbook

**Live URL:** https://oracle-aicoe.com/voice-drivethru/  ·  **VM:** `ubuntu@141.147.89.54`
**QR sign-up:** https://oracle-aicoe.com/voice-drivethru/signup
**Test member:** code **1234** (James Okafor, birthday = today). New sign-ups get a birthday too.

The agent is a single **OCI cascade** — OCI Speech (STT) → OCI Generative AI gpt-oss (LLM) → OCI Grok
(TTS), with Silero VAD + the LiveKit turn-detector — one brain, all Oracle.

## Start / stop
```
ssh ubuntu@141.147.89.54
sudo systemctl restart voicedt-livekit voicedt-web voicedt-agent   # restart all
systemctl is-active voicedt-livekit voicedt-web voicedt-agent      # check
journalctl -u voicedt-agent -f --output cat                        # live agent log
curl -s http://127.0.0.1:7871/api/healthz                          # {ok: true}
```
**After restarting voicedt-agent alone:** LiveKit only dispatches an agent on room *creation*, so a
kiosk tab left open keeps its old room alive and stays silent. Reload the kiosk page (its room
recreates → a fresh agent joins).

## If something looks off
| Symptom | Do this |
|---|---|
| Agent talks over itself / false barge-in | hand the customer the **Hold-to-talk** button (mutes the mic except while held) |
| Customer walked off | auto-resets on walk-off / after confirm; or tap **Start over / End** on the kiosk |
| Order on screen wrong | tap **Start over / End** to reset for the next customer |
| Agent silent / unresponsive | reload the kiosk page (re-dispatches the agent); if still bad, `systemctl restart voicedt-agent`, then reload the kiosk |
| No speech / no transcription | check `OCI_GENAI_API_KEY` (LLM + Grok TTS) and that `~/.oci/config` is present (OCI Speech STT); see `journalctl -u voicedt-agent` |

## Booth setup
- Booth Mac = kiosk browser only. Launch with `deploy/kiosk-launch.command` (Chrome `--kiosk`).
- Cardioid/close mic aimed away from the speaker; sane volume (WebRTC AEC handles echo, but help it).
- Network: venue Wi-Fi + 5G backup. All traffic is outbound.
- Media needs OCI security-list inbound **UDP 7882 + TCP 7881** (already opened).

## Secrets / config (VM `~/voice-order/.env`, chmod 600, gitignored)
- `OCI_GENAI_API_KEY` (+ `OCI_GENAI_REGION`, `OCI_GENAI_LLM_MODEL`, `OCI_GROK_VOICE`) — the LLM + Grok TTS.
- `~/.oci/config` (DEFAULT profile) on the host — OCI Speech STT auth (set `OCI_COMPARTMENT_ID` if not the tenancy root).
- `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` — must match `livekit.yaml`.
- ADB `DB_*` + wallet — membership (optional; guest mode without them).
- Languages: EN/ES/FR/HI/DE via `MULTILINGUAL` + `DEFAULT_LANGUAGE`. Edit `.env` and restart to change behaviour.

## Known follow-ups
- Verify OCI Speech + Grok TTS coverage for ES/FR/HI/DE on-site; drop any unsupported language from the sign-up picker.
- Real Oracle "O" mark + Redwood fonts (Brand) for the kiosk.
