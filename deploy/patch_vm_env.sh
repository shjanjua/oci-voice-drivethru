#!/usr/bin/env bash
# Runs on the VM: patch the scp'd .env for production (public LiveKit URL + creds). No secrets embedded here.
set -euo pipefail
cd /home/ubuntu/voice-order
python3 - <<'PY'
import re, os, secrets
p = ".env"; s = open(p).read()
def setkv(s, k, v):
    if re.search(rf"^{k}=.*$", s, re.M):
        return re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M)
    return s.rstrip() + f"\n{k}={v}\n"
s = setkv(s, "LIVEKIT_URL", "ws://localhost:7880")
s = setkv(s, "LIVEKIT_PUBLIC_URL", "wss://oracle-aicoe.com/voice-drivethru")
s = setkv(s, "LIVEKIT_API_KEY", "voicedt")
s = setkv(s, "LIVEKIT_API_SECRET", secrets.token_hex(24))
open(p, "w").write(s)
os.chmod(p, 0o600)
print("patched .env: LiveKit public/creds set")
PY
