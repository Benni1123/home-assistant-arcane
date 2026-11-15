"""Container lifecycle rules shared by buttons, the power switch and the action select."""

from __future__ import annotations

# Docker states in which each lifecycle action makes sense.
ACTION_STATES: dict[str, frozenset[str]] = {
    "start": frozenset({"created", "exited"}),
    "pause": frozenset({"running"}),
    "resume": frozenset({"paused"}),
    "restart": frozenset({"running", "created", "exited"}),
    "stop": frozenset({"running", "paused", "restarting"}),
}

# Presented as "on" by the power switch. A paused container still exists in memory
# and is stopped or resumed by switching it off or on.
ON_STATES = frozenset({"running", "restarting", "paused"})

# Order shown in the action select.
ACTION_ORDER = ("start", "resume", "restart", "pause", "stop")


def available_actions(state: str | None) -> list[str]:
    """Return the lifecycle actions allowed in a container state."""
    if not state:
        return []
    return [action for action in ACTION_ORDER if state in ACTION_STATES[action]]
