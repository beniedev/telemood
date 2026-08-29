"""Bot-scoped, dependency-free regular sticker catalogs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from os import PathLike
from threading import RLock
from typing import Protocol, runtime_checkable

from .contracts import (
    IncomingSticker,
    IncomingStickerEvent,
    RegularSticker,
    StickerFormat,
    StickerModelEvent,
    StickerModelView,
    StickerSenderKind,
    StickerType,
)


@runtime_checkable
class StickerCatalog(Protocol):
    def remember(self, sticker: RegularSticker | IncomingSticker) -> RegularSticker:
        ...

    def get(self, bot_namespace: str, file_unique_id: str) -> RegularSticker | None:
        ...

    def resolve(self, bot_namespace: str, catalog_id: str) -> RegularSticker | None:
        ...

    def list(self, bot_namespace: str) -> tuple[RegularSticker, ...]:
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
        catalog_id = sticker_catalog_id(sticker.bot_namespace, sticker.file_unique_id)
        if sticker.catalog_id is not None and sticker.catalog_id != catalog_id:
            raise ValueError("catalog_id does not match sticker identity")
        sticker = replace(sticker, catalog_id=catalog_id)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO stickers (
                    bot_namespace, file_unique_id, catalog_id, file_id, emoji,
                    set_name, sticker_type, sticker_format, thumbnail_ref, media_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_namespace, file_unique_id) DO UPDATE SET
                    catalog_id=excluded.catalog_id,
                    file_id=excluded.file_id,
                    emoji=excluded.emoji,
                    set_name=excluded.set_name,
                    sticker_type=excluded.sticker_type,
                    sticker_format=excluded.sticker_format,
                    thumbnail_ref=excluded.thumbnail_ref,
                    media_ref=excluded.media_ref
                """,
                (
                    sticker.bot_namespace,
                    sticker.file_unique_id,
                    sticker.catalog_id,
                    sticker.file_id,
                    sticker.emoji,
                    sticker.set_name,
                    sticker.type.value,
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
                SELECT bot_namespace, file_id, file_unique_id, catalog_id, emoji,
                       set_name, sticker_type, sticker_format, thumbnail_ref, media_ref
                FROM stickers
                WHERE bot_namespace = ? AND file_unique_id = ?
                """,
                (bot_namespace, file_unique_id),
            ).fetchone()
        return _row_to_sticker(row)

    def resolve(self, bot_namespace: str, catalog_id: str) -> RegularSticker | None:
        _validate_identity(bot_namespace, "bot_namespace")
        _validate_identity(catalog_id, "catalog_id")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT bot_namespace, file_id, file_unique_id, catalog_id, emoji,
                       set_name, sticker_type, sticker_format, thumbnail_ref, media_ref
                FROM stickers
                WHERE bot_namespace = ? AND catalog_id = ?
                """,
                (bot_namespace, catalog_id),
            ).fetchone()
        return _row_to_sticker(row)

    def list(self, bot_namespace: str) -> tuple[RegularSticker, ...]:
        _validate_identity(bot_namespace, "bot_namespace")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT bot_namespace, file_id, file_unique_id, catalog_id, emoji,
                       set_name, sticker_type, sticker_format, thumbnail_ref, media_ref
                FROM stickers
                WHERE bot_namespace = ?
                ORDER BY catalog_id
                """,
                (bot_namespace,),
            ).fetchall()
        return tuple(_row_to_sticker(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=self._timeout)
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stickers (
                bot_namespace TEXT NOT NULL,
                file_unique_id TEXT NOT NULL,
                catalog_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                emoji TEXT,
                set_name TEXT,
                sticker_type TEXT NOT NULL DEFAULT 'regular',
                sticker_format TEXT NOT NULL,
                thumbnail_ref TEXT,
                media_ref TEXT,
                PRIMARY KEY (bot_namespace, file_unique_id),
                UNIQUE (bot_namespace, catalog_id)
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(stickers)").fetchall()
        }
        if "catalog_id" not in columns:
            connection.execute("ALTER TABLE stickers ADD COLUMN catalog_id TEXT")
        if "sticker_type" not in columns:
            connection.execute(
                "ALTER TABLE stickers ADD COLUMN sticker_type TEXT NOT NULL DEFAULT 'regular'"
            )
        for bot_namespace, file_unique_id in connection.execute(
            "SELECT bot_namespace, file_unique_id FROM stickers WHERE catalog_id IS NULL"
        ):
            connection.execute(
                """
                UPDATE stickers SET catalog_id = ?
                WHERE bot_namespace = ? AND file_unique_id = ?
                """,
                (
                    sticker_catalog_id(bot_namespace, file_unique_id),
                    bot_namespace,
                    file_unique_id,
                ),
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS stickers_catalog_id
            ON stickers (bot_namespace, catalog_id)
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


def sticker_catalog_id(bot_namespace: str, file_unique_id: str) -> str:
    """Return the stable opaque id exposed to plans instead of Telegram file ids."""

    _validate_identity(bot_namespace, "bot_namespace")
    _validate_identity(file_unique_id, "file_unique_id")
    digest = sha256(f"{bot_namespace}\0{file_unique_id}".encode()).hexdigest()[:24]
    return f"sticker_{digest}"


def ingest_incoming_sticker(
    event: IncomingStickerEvent,
    catalog: StickerCatalog,
) -> StickerModelEvent:
    """Remember a supported sticker and return a safe, honest model-facing view."""

    if not isinstance(event, IncomingStickerEvent):
        raise TypeError("event must be IncomingStickerEvent")
    if not isinstance(catalog, StickerCatalog):
        raise TypeError("catalog must implement StickerCatalog")
    stored = catalog.remember(event.sticker)
    return StickerModelEvent(
        sticker=_model_view(stored),
        sender_kind=(
            StickerSenderKind.USER
            if event.sender_user_id is not None
            else StickerSenderKind.CHAT
        ),
        target_role=event.target.channel,
        in_thread=event.target.thread_id is not None,
        occurred_at=event.received_at,
    )


def list_sticker_model_views(
    catalog: StickerCatalog,
    bot_namespace: str,
) -> tuple[StickerModelView, ...]:
    """Return the bot-scoped sticker catalog without reusable provider ids."""

    if not isinstance(catalog, StickerCatalog):
        raise TypeError("catalog must implement StickerCatalog")
    return tuple(_model_view(sticker) for sticker in catalog.list(bot_namespace))


def _model_view(stored: RegularSticker) -> StickerModelView:
    details = [f"{stored.format.value} regular sticker"]
    if stored.emoji:
        details.append(f"emoji {stored.emoji}")
    if stored.set_name:
        details.append(f"set {stored.set_name}")
    if stored.thumbnail_ref is None and stored.media_ref is None:
        details.append("image content not attached")
    return StickerModelView(
        catalog_id=stored.catalog_id,
        text="; ".join(details),
        thumbnail_ref=stored.thumbnail_ref,
        media_ref=stored.media_ref,
    )


def _row_to_sticker(row) -> RegularSticker | None:
    if row is None:
        return None
    return RegularSticker(
        bot_namespace=row[0],
        file_id=row[1],
        file_unique_id=row[2],
        catalog_id=row[3],
        emoji=row[4],
        set_name=row[5],
        type=StickerType(row[6]),
        format=StickerFormat(row[7]),
        thumbnail_ref=row[8],
        media_ref=row[9],
    )
