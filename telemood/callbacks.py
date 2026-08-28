"""Bounded, one-shot callback state for interaction prompts."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from os import PathLike
from threading import RLock
from time import monotonic, time
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4

from .contracts import (
    CallbackPayload,
    CallbackRejection,
    CallbackToken,
    InteractionKind,
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
        thread_id: str | None = None,
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
        thread_id: str | None = None,
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
    thread_id: str | None = None
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
        deadline_clock: Callable[[], float] = time,
        token_factory: Callable[[], str] | None = None,
        max_entries: int = 1024,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._deadline_clock = deadline_clock
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
        thread_id: str | None = None,
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
        _validate_optional_identity(thread_id, "thread_id")
        with self._lock:
            self._purge_expired_locked()
            if len(self._entries) >= self._max_entries:
                raise ValueError("callback registry full")
            ttl = float(ttl_seconds)
            token = CallbackToken(
                self._token_factory(),
                expires_at=self._deadline_clock() + ttl,
            )
            if token.value in self._entries:
                raise ValueError("callback token collision")
            self._entries[token.value] = _CallbackEntry(
                token=token,
                user_id=user_id,
                chat_id=chat_id,
                payload=payload,
                expires_at=self._clock() + ttl,
                thread_id=thread_id,
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

    def activate_all(self, tokens: Sequence[CallbackToken]) -> bool:
        values = tuple(tokens)
        if not values:
            return True
        if not all(isinstance(token, CallbackToken) for token in values):
            return False
        with self._lock:
            entries = [self._entries.get(token.value) for token in values]
            if any(
                entry is None
                or self._expire_if_needed(entry)
                or entry.state != "pending"
                for entry in entries
            ):
                return False
            for entry in entries:
                entry.state = "active"
            return True

    def revoke_all(self, tokens: Sequence[CallbackToken]) -> bool:
        values = tuple(tokens)
        if not values:
            return True
        if not all(isinstance(token, CallbackToken) for token in values):
            return False
        with self._lock:
            entries = [self._entries.get(token.value) for token in values]
            if any(
                entry is None
                or self._expire_if_needed(entry)
                or entry.state in {"revoked", "used"}
                for entry in entries
            ):
                return False
            for entry in entries:
                entry.state = "revoked"
            return True

    def consume(
        self,
        token: CallbackToken,
        *,
        user_id: str,
        chat_id: str,
        thread_id: str | None = None,
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
            if entry.thread_id != thread_id:
                return CallbackResolution(False, CallbackRejection.THREAD_MISMATCH)
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


def _validate_optional_identity(value: str | None, field_name: str) -> None:
    if value is not None and (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field_name} must be non-empty when provided")


class SQLiteCallbackStore:
    """Durable callback store with SQLite transaction-level one-shot claims."""

    def __init__(
        self,
        path: str | PathLike[str],
        *,
        clock: Callable[[], float] = time,
        token_factory: Callable[[], str] | None = None,
        max_entries: int = 1024,
        timeout: float = 5.0,
    ) -> None:
        if path is None or not str(path) or str(path) == ":memory:":
            raise ValueError("callback store path is required")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._path = str(path)
        self._clock = clock
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._max_entries = max_entries
        self._timeout = float(timeout)
        self._lock = RLock()
        self._initialize()

    def register(
        self,
        *,
        user_id: str,
        chat_id: str,
        payload: CallbackPayload,
        ttl_seconds: float,
        thread_id: str | None = None,
    ) -> CallbackToken:
        _validate_identity(user_id, "user_id")
        _validate_identity(chat_id, "chat_id")
        _validate_optional_identity(thread_id, "thread_id")
        if not isinstance(payload, CallbackPayload):
            raise ValueError("payload must be CallbackPayload")
        if not isinstance(ttl_seconds, (int, float)) or not isfinite(float(ttl_seconds)):
            raise ValueError("ttl_seconds must be finite")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._purge_expired(connection, now)
            count = connection.execute("SELECT COUNT(*) FROM callbacks").fetchone()[0]
            if count >= self._max_entries:
                connection.rollback()
                raise ValueError("callback store full")
            expires_at = now + float(ttl_seconds)
            token = CallbackToken(self._token_factory(), expires_at=expires_at)
            try:
                connection.execute(
                    """
                    INSERT INTO callbacks (
                        token, user_id, chat_id, thread_id, kind, request_id,
                        value, expires_at, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        token.value,
                        user_id,
                        chat_id,
                        thread_id,
                        payload.kind.value,
                        payload.request_id,
                        payload.value,
                        expires_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ValueError("callback token collision") from exc
            return token

    def activate(self, token: CallbackToken) -> bool:
        if not isinstance(token, CallbackToken):
            return False
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE callbacks SET state = 'active'
                WHERE token = ? AND state = 'pending' AND expires_at > ?
                """,
                (token.value, now),
            ).rowcount
            if changed:
                return True
            self._mark_expired(connection, token.value, now)
            return False

    def activate_all(self, tokens: Sequence[CallbackToken]) -> bool:
        values = tuple(tokens)
        if not values:
            return True
        if not all(isinstance(token, CallbackToken) for token in values):
            return False
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = [
                connection.execute(
                    "SELECT state, expires_at FROM callbacks WHERE token = ?",
                    (token.value,),
                ).fetchone()
                for token in values
            ]
            if any(
                row is None or row[0] != "pending" or float(row[1]) <= now
                for row in rows
            ):
                for token, row in zip(values, rows):
                    if row is not None and float(row[1]) <= now:
                        self._mark_expired(connection, token.value, now)
                return False
            connection.executemany(
                "UPDATE callbacks SET state = 'active' WHERE token = ?",
                ((token.value,) for token in values),
            )
            return True

    def revoke(self, token: CallbackToken) -> bool:
        if not isinstance(token, CallbackToken):
            return False
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE callbacks SET state = 'revoked'
                WHERE token = ? AND state IN ('pending', 'active') AND expires_at > ?
                """,
                (token.value, now),
            ).rowcount
            if changed:
                return True
            self._mark_expired(connection, token.value, now)
            return False

    def revoke_all(self, tokens: Sequence[CallbackToken]) -> bool:
        values = tuple(tokens)
        if not values:
            return True
        if not all(isinstance(token, CallbackToken) for token in values):
            return False
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = [
                connection.execute(
                    "SELECT state, expires_at FROM callbacks WHERE token = ?",
                    (token.value,),
                ).fetchone()
                for token in values
            ]
            if any(
                row is None
                or row[0] not in {"pending", "active"}
                or float(row[1]) <= now
                for row in rows
            ):
                for token, row in zip(values, rows):
                    if row is not None and float(row[1]) <= now:
                        self._mark_expired(connection, token.value, now)
                return False
            connection.executemany(
                "UPDATE callbacks SET state = 'revoked' WHERE token = ?",
                ((token.value,) for token in values),
            )
            return True

    def consume(
        self,
        token: CallbackToken,
        *,
        user_id: str,
        chat_id: str,
        thread_id: str | None = None,
    ) -> CallbackResolution:
        if not isinstance(token, CallbackToken):
            return CallbackResolution(False, CallbackRejection.UNKNOWN)
        _validate_identity(user_id, "user_id")
        _validate_identity(chat_id, "chat_id")
        _validate_optional_identity(thread_id, "thread_id")
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT user_id, chat_id, thread_id, kind, request_id, value,
                       expires_at, state
                FROM callbacks WHERE token = ?
                """,
                (token.value,),
            ).fetchone()
            if row is None:
                return CallbackResolution(False, CallbackRejection.UNKNOWN)
            if float(row[6]) <= now:
                self._mark_expired(connection, token.value, now)
                return CallbackResolution(False, CallbackRejection.EXPIRED)
            if row[0] != user_id:
                return CallbackResolution(False, CallbackRejection.USER_MISMATCH)
            if row[1] != chat_id:
                return CallbackResolution(False, CallbackRejection.CHAT_MISMATCH)
            if row[2] != thread_id:
                return CallbackResolution(False, CallbackRejection.THREAD_MISMATCH)
            if row[7] == "revoked":
                return CallbackResolution(False, CallbackRejection.REVOKED)
            if row[7] == "used":
                return CallbackResolution(False, CallbackRejection.REPLAY)
            if row[7] != "active":
                return CallbackResolution(False, CallbackRejection.PENDING)
            changed = connection.execute(
                "UPDATE callbacks SET state = 'used' WHERE token = ? AND state = 'active'",
                (token.value,),
            ).rowcount
            if changed != 1:
                return CallbackResolution(False, CallbackRejection.REPLAY)
            return CallbackResolution(
                True,
                payload=CallbackPayload(
                    kind=InteractionKind(row[3]),
                    request_id=row[4],
                    value=row[5],
                ),
            )

    def purge_expired(self) -> int:
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._purge_expired(connection, now)

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=self._timeout)
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS callbacks (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id TEXT,
                kind TEXT NOT NULL,
                request_id TEXT NOT NULL,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL,
                state TEXT NOT NULL
            )
            """
        )
        connection.commit()
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _purge_expired(connection: sqlite3.Connection, now: float) -> int:
        return connection.execute(
            "DELETE FROM callbacks WHERE expires_at <= ?", (now,)
        ).rowcount

    @staticmethod
    def _mark_expired(connection: sqlite3.Connection, token: str, now: float) -> None:
        connection.execute(
            "UPDATE callbacks SET state = 'expired' WHERE token = ? AND expires_at <= ?",
            (token, now),
        )


def _validate_identity(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field_name} must be non-empty")
