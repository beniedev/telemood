"""Optional, harness-neutral inbound message coalescing policy.

A user often splits one thought across several consecutive messages, or sends
a sticker and then the text that goes with it.  This module gives the host a
pure policy surface for merging those consecutive inbound parts into one
logical turn: it reads no clock, performs no I/O, and owns no timers.  The
host keeps per-conversation buffers, scheduling, and turn cancellation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from typing import Literal


def _positive_finite_seconds(name: str, value: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number of seconds")


@dataclass(frozen=True)
class CoalescePolicy:
    """Timing bounds for merging consecutive inbound parts into one batch.

    ``quiet_seconds`` is the required silence after a text part before the
    batch flushes.  ``sticker_quiet_seconds`` is the longer silence after a
    sticker part, because a sticker is often followed by an explanatory text.
    ``max_wait_seconds`` caps the whole batch measured from the FIRST part's
    ``received_at`` so a continuous trickle of parts cannot defer a turn
    forever.
    """

    quiet_seconds: float = 2.5
    sticker_quiet_seconds: float = 4.5
    max_wait_seconds: float = 8.0

    def __post_init__(self) -> None:
        _positive_finite_seconds("quiet_seconds", self.quiet_seconds)
        _positive_finite_seconds("sticker_quiet_seconds", self.sticker_quiet_seconds)
        _positive_finite_seconds("max_wait_seconds", self.max_wait_seconds)
        if self.max_wait_seconds < max(self.quiet_seconds, self.sticker_quiet_seconds):
            raise ValueError("max_wait_seconds must cover the largest quiet window")

    def quiet_for(self, kind: str) -> float:
        """Return the quiet window that applies after a part of ``kind``."""
        if kind == "sticker":
            return self.sticker_quiet_seconds
        return self.quiet_seconds


@dataclass(frozen=True)
class InboundPart:
    """One normalized inbound message part with host-supplied timing.

    ``text`` is the host-rendered text for this part; for a sticker it is the
    host's sticker description.  ``received_at`` is a host monotonic timestamp
    in seconds; this module never reads a clock itself.
    """

    kind: Literal["text", "sticker"]
    text: str
    received_at: float


class InboundCoalescer:
    """Pending-batch state for one conversation's consecutive inbound parts.

    The batch flushes at the earlier of two deadlines: the quiet window after
    the newest part, or ``max_wait_seconds`` measured from the first part.
    ``add`` returns the new flush deadline so the host can schedule or
    reschedule its own timer.
    """

    def __init__(self, policy: CoalescePolicy | None = None) -> None:
        self._policy = policy if policy is not None else CoalescePolicy()
        self._parts: list[InboundPart] = []

    @property
    def policy(self) -> CoalescePolicy:
        return self._policy

    def add(self, part: InboundPart) -> float:
        """Append ``part`` and return the new flush deadline.

        ``part.received_at`` must not be earlier than the previous part's;
        arrival order is the merge order.
        """
        if self._parts and part.received_at < self._parts[-1].received_at:
            raise ValueError("received_at must not decrease across parts")
        self._parts.append(part)
        deadline = self.deadline()
        assert deadline is not None
        return deadline

    def deadline(self) -> float | None:
        """Return the earliest flush time, or ``None`` when the batch is empty."""
        if not self._parts:
            return None
        first = self._parts[0]
        last = self._parts[-1]
        quiet_deadline = last.received_at + self._policy.quiet_for(last.kind)
        cap_deadline = first.received_at + self._policy.max_wait_seconds
        return min(quiet_deadline, cap_deadline)

    def due(self, now: float) -> bool:
        """Return ``True`` when a pending batch has reached its deadline."""
        deadline = self.deadline()
        return deadline is not None and now >= deadline

    def drain(self) -> tuple[InboundPart, ...]:
        """Return all pending parts in arrival order and clear the batch."""
        parts = tuple(self._parts)
        self._parts.clear()
        return parts

    def __len__(self) -> int:
        return len(self._parts)


def render_inbound(parts: Iterable[InboundPart]) -> str:
    """Render a drained batch into one logical inbound message.

    Arrival order is preserved; each part's ``text`` is stripped and empty
    parts are skipped.  No labels are added: the host already rendered each
    part, including sticker descriptions.
    """
    return "\n".join(text for part in parts if (text := part.text.strip()))


def can_restart(*, visible_output_sent: bool, side_effect_tool_started: bool) -> bool:
    """Return whether a running turn may still be cancelled and re-run."""
    return not visible_output_sent and not side_effect_tool_started


class RestartGuard:
    """Tracks one running turn: may the host cancel it and re-run merged input?

    A turn may only be restarted while it is still invisible to the user:
    no bubble, message, or reaction has been delivered
    (:meth:`mark_visible_output`) and no tool with side effects has started
    (:meth:`observe_tool`).  The host declares which tool names are
    side-effect-free via ``read_only_tools``.
    """

    def __init__(self, read_only_tools: Iterable[str] = ()) -> None:
        self._read_only_tools = frozenset(read_only_tools)
        self._visible_output_sent = False
        self._side_effect_tool_started = False

    def mark_visible_output(self) -> None:
        """Record that user-visible output was delivered for this turn."""
        self._visible_output_sent = True

    def observe_tool(self, name: str) -> None:
        """Record a tool call start; unknown names count as side effects."""
        if name not in self._read_only_tools:
            self._side_effect_tool_started = True

    @property
    def can_restart(self) -> bool:
        return can_restart(
            visible_output_sent=self._visible_output_sent,
            side_effect_tool_started=self._side_effect_tool_started,
        )
