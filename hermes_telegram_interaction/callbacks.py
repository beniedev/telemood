"""Bounded, one-shot callback state for interaction prompts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from threading import RLock
from time import monotonic
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4

from .contracts import (
    CallbackPayload,
    CallbackRejection,
    CallbackToken,
)


@dataclass(frozen=True)
class CallbackResolution:
    accepted: bool
    reason: CallbackRejection | None = None
    payload: CallbackPayload | None = None

    def __post_init__(self) -> None:
        if self.accepted:
            if self.reason is not None or self.payload is None:
                raise ValueError("accepted callback must contain only a payload")
        elif self.reason is None or self.payload is not None:
            raise ValueError("rejected callback must contain only a reason")



@runtime_checkable
class CallbackStore(Protocol):
    """Callback state seam shared by in-memory and durable stores.

    The kernel uses singular activation/revocation. Durable stores may expose
    atomic activate_all/revoke_all operations instead; the kernel adapts those
    operations without weakening the protocol gate.
    """

    def register(
        self,
        *,
        user_id: str,
        chat_id: str,
        payload: CallbackPayload,
        ttl_seconds: float,
    ) -> CallbackToken:
        ...

    def activate(self, token: CallbackToken) -> bool:
        ...

    def revoke(self, token: CallbackToken) -> bool:
        ...

    def consume(
        self,
        token: CallbackToken,
        *,
        user_id: str,
        chat_id: str,
    ) -> CallbackResolution:
        ...

    @classmethod
    def __subclasshook__(cls, candidate: type[object]):
        if cls is not CallbackStore:
            return NotImplemented

        def declares(name: str) -> bool:
            return any(
                name in base.__dict__ and callable(base.__dict__[name])
                for base in candidate.__mro__
            )

        if not all(declares(name) for name in ("register", "consume")):
            return NotImplemented
        if (declares("activate") and declares("revoke")) or (
            declares("activate_all") and declares("revoke_all")
        ):
            return True
        return NotImplemented


@dataclass
class _CallbackEntry:
    token: CallbackToken
    user_id: str
    chat_id: str
    payload: CallbackPayload
    expires_at: float
    state: str = "pending"


class CallbackRegistry:
    """In-memory registry with explicit activation and one-shot consumption.

    A callback is pending until its host delivery is verified.  This prevents
    an uncertain or failed prompt from becoming an accepted visible action.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        token_factory: Callable[[], str] | None = None,
        max_entries: int = 1024,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._max_entries = max_entries
        self._lock = RLock()
        self._entries: dict[str, _CallbackEntry] = {}

    def register(
        self,
        *,
        user_id: str,
        chat_id: str,
        payload: CallbackPayload,
        ttl_seconds: float,
    ) -> CallbackToken:
        if (
            not isinstance(user_id, str)
            or not user_id
            or user_id != user_id.strip()
            or any(ord(character) < 32 for character in user_id)
        ):
            raise ValueError("user_id must be non-empty")
        if (
            not isinstance(chat_id, str)
            or not chat_id
            or chat_id != chat_id.strip()
            or any(ord(character) < 32 for character in chat_id)
        ):
            raise ValueError("chat_id must be non-empty")
        if not isfinite(float(ttl_seconds)) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not isinstance(payload, CallbackPayload):
            raise ValueError("payload must be CallbackPayload")
        with self._lock:
            self._purge_expired_locked()
            if len(self._entries) >= self._max_entries:
                raise ValueError("callback registry full")
            token = CallbackToken(self._token_factory())
            if token.value in self._entries:
                raise ValueError("callback token collision")
            self._entries[token.value] = _CallbackEntry(
                token=token,
                user_id=user_id,
                chat_id=chat_id,
                payload=payload,
                expires_at=self._clock() + float(ttl_seconds),
            )
            return token

    def activate(self, token: CallbackToken) -> bool:
        if not isinstance(token, CallbackToken):
            return False
        with self._lock:
            entry = self._entries.get(token.value)
            if entry is None or self._expire_if_needed(entry):
                return False
            if entry.state != "pending":
                return False
            entry.state = "active"
            return True

    def revoke(self, token: CallbackToken) -> bool:
        if not isinstance(token, CallbackToken):
            return False
        with self._lock:
            entry = self._entries.get(token.value)
            if entry is None or self._expire_if_needed(entry):
                return False
            if entry.state in {"revoked", "used"}:
                return False
            entry.state = "revoked"
            return True

    def consume(
        self,
        token: CallbackToken,
        *,
        user_id: str,
        chat_id: str,
    ) -> CallbackResolution:
        if not isinstance(token, CallbackToken):
            return CallbackResolution(False, CallbackRejection.UNKNOWN)
        with self._lock:
            entry = self._entries.get(token.value)
            if entry is None:
                return CallbackResolution(False, CallbackRejection.UNKNOWN)
            if self._expire_if_needed(entry):
                return CallbackResolution(False, CallbackRejection.EXPIRED)
            if entry.user_id != user_id:
                return CallbackResolution(False, CallbackRejection.USER_MISMATCH)
            if entry.chat_id != chat_id:
                return CallbackResolution(False, CallbackRejection.CHAT_MISMATCH)
            if entry.state == "revoked":
                return CallbackResolution(False, CallbackRejection.REVOKED)
            if entry.state == "used":
                return CallbackResolution(False, CallbackRejection.REPLAY)
            if entry.state != "active":
                return CallbackResolution(False, CallbackRejection.PENDING)
            entry.state = "used"
            return CallbackResolution(True, payload=entry.payload)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_locked()

    def _purge_expired_locked(self) -> int:
        expired = [
            token
            for token, entry in self._entries.items()
            if self._expire_if_needed(entry)
        ]
        for token in expired:
            del self._entries[token]
        return len(expired)

    def _expire_if_needed(self, entry: _CallbackEntry) -> bool:
        if entry.state == "expired":
            return True
        if self._clock() >= entry.expires_at:
            entry.state = "expired"
            return True
        return False
