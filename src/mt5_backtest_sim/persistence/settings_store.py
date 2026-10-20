"""Persistent settings.

The file format is the one the reference simulator uses: a gzip-compressed UTF-8 JSON
document named ``simulator_settings.dat``. Keeping the container format means a
settings file can be read by either tool, and it keeps the file small and inspectable
with ordinary tools.

The content is this project's own schema. It deliberately does **not** carry licence
or activation fields: this simulator has no licence check, and storing a credential it
never uses would be worse than useless.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from mt5_backtest_sim.domain.exceptions import SimulationError

__all__ = ["Settings", "load_settings", "save_settings", "settings_path"]

SETTINGS_FILENAME = "simulator_settings.dat"


@dataclass(slots=True)
class Settings:
    """The settings of the simulator.

    Defaults mirror the reference product so that a user moving between the two
    tools finds the same behaviour.
    """

    data_folder: str = ""
    account_currency: str = "USD"
    buy_button_first: bool = True
    always_on_top: bool = False
    atr_period: int = 14
    modify_button_location: str = "RIGHT_SIDE"
    live_mode_lookback_months: int = 3
    default_pending_distance: int = 5
    default_sl_distance: int = 5
    default_tp_distance: int = 5
    hotkey_modifier: str = "Ctrl"
    hotkey_play_pause: str = "Space"
    hotkey_increase_speed: str = "Up"
    hotkey_decrease_speed: str = "Down"
    hotkey_next_bar: str = "Right"
    hotkey_previous_bar: str = "Left"
    hotkey_buy: str = "B"
    hotkey_sell: str = "S"
    hotkey_close_losing: str = "L"
    hotkey_close_winning: str = "W"
    hotkey_close_all: str = "A"
    last_selected_symbols: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """The settings as a JSON-ready mapping."""
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Settings:
        """Rebuild settings from a JSON mapping, ignoring unknown keys."""
        known = {key: value for key, value in payload.items() if key in cls.__slots__}
        return cls(**known)


def settings_path(directory: str | Path | None = None) -> Path:
    """The path of the settings file.

    By default the file sits next to the executable, which is where the reference
    product keeps it.
    """
    if directory is None:
        import sys

        base = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
    else:
        base = Path(directory)
    return base / SETTINGS_FILENAME


def save_settings(settings: Settings, path: str | Path | None = None) -> Path:
    """Write the settings to disk and return the path."""
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.to_json(), indent=2, sort_keys=True)
    with gzip.open(target, "wb", compresslevel=1) as handle:
        handle.write(payload.encode("utf-8"))
    return target


def load_settings(path: str | Path | None = None) -> Settings:
    """Read the settings from disk.

    A missing file yields the defaults: a first run must not fail because nothing has
    been saved yet.
    """
    target = Path(path) if path is not None else settings_path()
    if not target.is_file():
        return Settings()
    try:
        with gzip.open(target, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise SimulationError(f"cannot read settings from {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SimulationError(f"settings file {target} does not hold an object")
    return Settings.from_json(payload)


def data_folder_default() -> Path:
    """The default folder for tick data, as the reference product uses it."""
    from pathlib import Path as _Path

    return _Path.home() / "Documents" / "ForexSimulator_Data"


def decimal_or_zero(value: Any) -> Decimal:
    """Read a decimal that may have been stored as a string."""
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal(0)
    return Decimal(str(value))
