"""Bot-scoped, dependency-free regular sticker catalogs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from os import PathLike
from threading import RLock
from typing import Protocol, runtime_checkable

from .contracts import IncomingSticker, RegularSticker, StickerFormat


@runtime_checkable
class StickerCatalog(Protocol):
    def remember(self, sticker: RegularSticker | IncomingSticker) -> RegularSticker:
        ...

    def get(self, bot_namespace: str, file_unique_id: str) -> RegularSticker | None:
        ...


class SQLiteStickerCatalog:
    """A regenerable catalog keyed by bot namespace and unique sticker id."""

    def __init__(self, path: str | PathLike[str], *, timeout: float = 5.0) -> None:
        if path is None or not str(path) or str(path) == ":memory:":
            raise ValueError("sticker catalog path is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._path = str(path)
        self._timeout = float(timeout)
        self._lock = RLock()
        self._initialize()

    def remember(self, sticker: RegularSticker | IncomingSticker) -> RegularSticker:
        if isinstance(sticker, IncomingSticker):
            sticker = sticker.as_regular()
        if not isinstance(sticker, RegularSticker):
            raise TypeError("sticker must be RegularSticker or IncomingSticker")
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO stickers (
                    bot_namespace, file_unique_id, file_id, emoji, set_name,
                    sticker_format, thumbnail_ref, media_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_namespace, file_unique_id) DO UPDATE SET
                    file_id=excluded.file_id,
                    emoji=excluded.emoji,
                    set_name=excluded.set_name,
                    sticker_format=excluded.sticker_format,
                    thumbnail_ref=excluded.thumbnail_ref,
                    media_ref=excluded.media_ref
                """,
                (
                    sticker.bot_namespace,
                    sticker.file_unique_id,
                    sticker.file_id,
                    sticker.emoji,
                    sticker.set_name,
                    sticker.format.value,
                    sticker.thumbnail_ref,
                    sticker.media_ref,
                ),
            )
        return sticker

    def get(self, bot_namespace: str, file_unique_id: str) -> RegularSticker | None:
        _validate_identity(bot_namespace, "bot_namespace")
        _validate_identity(file_unique_id, "file_unique_id")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT bot_namespace, file_id, file_unique_id, emoji, set_name,
                       sticker_format, thumbnail_ref, media_ref
                FROM stickers
                WHERE bot_namespace = ? AND file_unique_id = ?
                """,
                (bot_namespace, file_unique_id),
            ).fetchone()
        if row is None:
            return None
        return RegularSticker(
            bot_namespace=row[0],
            file_id=row[1],
            file_unique_id=row[2],
            emoji=row[3],
            set_name=row[4],
            format=StickerFormat(row[5]),
            thumbnail_ref=row[6],
            media_ref=row[7],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=self._timeout)
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stickers (
                bot_namespace TEXT NOT NULL,
                file_unique_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                emoji TEXT,
                set_name TEXT,
                sticker_format TEXT NOT NULL,
                thumbnail_ref TEXT,
                media_ref TEXT,
                PRIMARY KEY (bot_namespace, file_unique_id)
            )
            """
        )
        connection.commit()
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.commit()

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


def _validate_identity(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
