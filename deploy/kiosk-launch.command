#!/bin/bash
# Booth kiosk launcher (double-click on the booth Mac). Opens the drive-thru in a locked-down
# Chrome kiosk: fullscreen, no chrome UI, mic auto-granted, autoplay allowed, no crash bubbles.
URL="https://oracle-aicoe.com/voice-drivethru/"
open -na "Google Chrome" --args \
  --kiosk --app="$URL" \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  --disable-features=TranslateUI \
  --disable-pinch --overscroll-history-navigation=0 \
  --noerrdialogs --disable-session-crashed-bubble --disable-infobars
