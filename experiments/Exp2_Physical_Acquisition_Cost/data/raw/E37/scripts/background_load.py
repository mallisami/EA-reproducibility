#!/usr/bin/env python3
"""
E37 moderate API-load generator (L1 condition). Issues GETs against an
UNRELATED object (obs-irrelevant) at a fixed rate on a background thread so
it never touches the objects/data under measurement. Started/stopped by
run_e21.py around the L1 blocks only; L0 (idle) blocks run with this off.
"""
import json, threading, time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
_PROTOCOL = json.loads((ROOT / "E37_PROTOCOL.json").read_text())
PROXY = f"http://127.0.0.1:{_PROTOCOL['proxy_port']}"
NS = _PROTOCOL["namespace"]
TARGET_RPS = int(_PROTOCOL["background_load_rate_rps"])


class BackgroundLoad:
    def __init__(self, rps=TARGET_RPS):
        self.rps = rps
        self._stop = threading.Event()
        self._thread = None
        self.requests_sent = 0

    def _loop(self):
        interval = 1.0 / self.rps
        url = f"{PROXY}/api/v1/namespaces/{NS}/configmaps/obs-irrelevant"
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                with urlopen(url, timeout=2) as r:
                    r.read()
                self.requests_sent += 1
            except Exception:
                pass
            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, interval - elapsed))

    def start(self):
        self._stop.clear()
        self.requests_sent = 0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        return self.requests_sent


if __name__ == "__main__":
    bl = BackgroundLoad()
    bl.start()
    time.sleep(10)
    n = bl.stop()
    print(f"sent {n} requests in 10s (~{n/10:.1f} rps)")
