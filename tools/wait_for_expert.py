"""Wait for the expert to connect and exchange messages with it.

Run this while the terminal is open. Attach the ForexSimulatorBridge expert
to any chart in the terminal; the expert connects to the named pipe this
script creates.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mt5_backtest_sim.integrations.mt5.session import Mt5Session  # noqa: E402

PIPE = "mt5_backtest_sim"
WAIT_SECONDS = 300


def main() -> int:
    """Wait, then exchange messages."""
    print(f"waiting up to {WAIT_SECONDS} seconds for the expert on {PIPE} ...", flush=True)
    session = Mt5Session(pipe_name=PIPE)
    started = time.monotonic()
    try:
        session.connect()
    except Exception as exc:  # noqa: BLE001 - the report needs the reason
        print(f"no expert answered: {type(exc).__name__}: {exc}")
        return 1

    print(f"connected after {time.monotonic() - started:.1f}s", flush=True)
    with session:
        print(f"ping answered   : {session.ping()}", flush=True)
        atr = session.request_atr("EURUSD", 1, 14)
        print(f"ATR(EURUSD, 14) : {atr:.8f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
