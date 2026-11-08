#!/usr/bin/env sh
# Start LocalVault AI (macOS/Linux). Opens http://127.0.0.1:8080
cd "$(dirname "$0")" && ( sleep 1; (command -v open >/dev/null && open http://127.0.0.1:8080) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8080) ) >/dev/null 2>&1 & exec python3 server.py "$@"
