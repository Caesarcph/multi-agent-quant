"""The manual kill switch, shared between the CLI and running processes.

The state is a small JSON file, written atomically, so ``maq kill-switch``
in one terminal stops the loop running in another on its next check. A file
that exists but cannot be read counts as engaged: when in doubt, stop.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from maq_risk.models import KillSwitchMode, KillSwitchState

ENV_VAR = "MAQ_KILL_SWITCH_FILE"
DEFAULT_PATH = Path("logs") / "kill_switch.json"


def default_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env[ENV_VAR]) if env.get(ENV_VAR) else DEFAULT_PATH


class KillSwitch:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()

    def engage(
        self,
        mode: KillSwitchMode = KillSwitchMode.HALT_NEW,
        reason: str = "",
        *,
        by: str = "cli",
        now: datetime | None = None,
    ) -> KillSwitchState:
        state = KillSwitchState(
            mode=mode,
            reason=reason or "manual stop",
            engaged_at=now or datetime.now(UTC),
            engaged_by=by,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".kill_switch.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(state.model_dump_json(indent=2))
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return state

    def release(self) -> bool:
        """Disengage; returns whether it was engaged."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def state(self) -> KillSwitchState | None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            return self._unreadable(f"cannot read {self.path}: {error}")
        try:
            return KillSwitchState.model_validate(json.loads(text))
        except (ValueError, ValidationError) as error:
            return self._unreadable(f"{self.path} is not a valid kill switch file: {error}")

    @property
    def engaged(self) -> bool:
        return self.state() is not None

    def _unreadable(self, reason: str) -> KillSwitchState:
        return KillSwitchState(
            mode=KillSwitchMode.HALT_NEW,
            reason=reason,
            engaged_at=datetime.now(UTC),
            engaged_by="kill-switch-file",
        )
