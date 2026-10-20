"""Live check of the bridge against the installed terminal.

The script starts the simulator side of the bridge in a thread, launches the
terminal with a configuration that attaches the expert to a chart, and reports
whether the expert answered.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, "src")

from mt5_backtest_sim.integrations.mt5.session import Mt5Session  # noqa: E402

TERMINAL = Path(r"C:\Program Files\Alpari MT5_2\terminal64.exe")
CONFIG = Path(__file__).resolve().parent / "mt5_bridge_test.ini"
PIPE = "mt5_backtest_sim"


def main() -> int:
    """Run the check."""
    outcome: dict[str, object] = {}

    def serve() -> None:
        try:
            with Mt5Session(pipe_name=PIPE) as session:
                outcome["connected"] = True
                outcome["ping"] = session.ping()
                outcome["atr"] = session.request_atr("EURUSD", 1, 14)
        except Exception as exc:  # noqa: BLE001 - the report needs the reason
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    print("launching the terminal ...")
    process = subprocess.Popen(
        [str(TERMINAL), f"/config:{CONFIG}"],
        cwd=str(TERMINAL.parent),
    )
    try:
        thread.join(timeout=90)
    finally:
        time.sleep(1)
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

    if "error" in outcome:
        print(f"the bridge did not complete: {outcome['error']}")
        return 1
    print(f"connected   : {outcome.get('connected')}")
    print(f"ping        : {outcome.get('ping')}")
    print(f"ATR(EURUSD) : {outcome.get('atr')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
