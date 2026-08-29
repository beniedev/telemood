from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from telemood import (
    ActionPlanError,
    AsyncInteractionKernel,
    DeliveryStatus,
    InteractionKernel,
    PLAN_VERSION,
    RegularSticker,
    SQLiteStickerCatalog,
    StickerType,
    TargetRef,
    TransportReceipt,
    action_plan_to_reply,
    ingest_incoming_sticker,
    list_sticker_model_views,
    normalize_incoming_sticker,
)


BOT_NAMESPACE = "bot-alpha"
OTHER_BOT_NAMESPACE = "bot-beta"
PROVIDER_FILE_ID = "synthetic-provider-file"
PROVIDER_UNIQUE_ID = "synthetic-provider-unique"
THUMBNAIL_REF = "host-media/sticker-thumbnail"
MEDIA_REF = "host-media/sticker-full"


def _target() -> TargetRef:
    return TargetRef(
        channel="telegram",
        chat_id="-100123",
        message_id="41",
        thread_id="9",
    )


def _sticker_update(*, sticker_type: str = "regular") -> dict[str, object]:
    return {
        "message": {
            "message_id": 41,
            "message_thread_id": 9,
            "date": 1_700_000_000,
            "chat": {"id": -100123},
            "from": {"id": 456},
            "sticker": {
                "file_id": PROVIDER_FILE_ID,
                "file_unique_id": PROVIDER_UNIQUE_ID,
                "type": sticker_type,
                "is_animated": False,
                "is_video": False,
                "emoji": "🙂",
                "set_name": "synthetic-user-pack",
            },
        }
    }


def _sticker_plan(catalog_id: str) -> dict[str, object]:
    return {
        "version": PLAN_VERSION,
        "actions": [
            {
                "type": "sticker",
                "sticker": {"kind": "catalog", "id": catalog_id},
            }
        ],
    }


class SyncStickerHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def send_sticker_sequence(self, request_id, request, parts):
        self.calls.append(
            (request_id, request.sticker_ref, tuple(part.value for part in parts))
        )
        return tuple(
            TransportReceipt(
                DeliveryStatus.VERIFIED,
                f"synthetic-delivery-{request_id}-{index}",
            )
            for index, _part in enumerate(parts)
        )


class AsyncStickerHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    async def send_sticker_sequence(self, request_id, request, parts):
        self.calls.append(
            (request_id, request.sticker_ref, tuple(part.value for part in parts))
        )
        return tuple(
            TransportReceipt(
                DeliveryStatus.VERIFIED,
                f"synthetic-async-delivery-{request_id}-{index}",
            )
            for index, _part in enumerate(parts)
        )


class StickerRoundTripTests(unittest.TestCase):
    def _ingest(self, catalog: SQLiteStickerCatalog):
        event = normalize_incoming_sticker(
            _sticker_update(),
            bot_namespace=BOT_NAMESPACE,
            thumbnail_ref=THUMBNAIL_REF,
            media_ref=MEDIA_REF,
        )
        return event, ingest_incoming_sticker(event, catalog)

    def test_normalize_and_ingest_expose_host_media_and_hide_provider_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            event, model_event = self._ingest(catalog)

            self.assertEqual(event.sticker.type, StickerType.REGULAR)
            self.assertEqual(event.sticker.thumbnail_ref, THUMBNAIL_REF)
            self.assertEqual(event.sticker.media_ref, MEDIA_REF)
            self.assertEqual(model_event.sticker.thumbnail_ref, THUMBNAIL_REF)
            self.assertEqual(model_event.sticker.media_ref, MEDIA_REF)
            self.assertIn("regular sticker", model_event.sticker.text)
            self.assertIn("synthetic-user-pack", model_event.sticker.text)
            self.assertTrue(model_event.sticker.catalog_id.startswith("sticker_"))
            self.assertNotIn(PROVIDER_FILE_ID, repr(model_event))
            self.assertNotIn(PROVIDER_UNIQUE_ID, repr(model_event))

            stored = catalog.resolve(BOT_NAMESPACE, model_event.sticker.catalog_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.thumbnail_ref, THUMBNAIL_REF)
            self.assertEqual(stored.media_ref, MEDIA_REF)
            self.assertEqual(catalog.list(BOT_NAMESPACE), (stored,))
            self.assertEqual(
                list_sticker_model_views(catalog, BOT_NAMESPACE),
                (model_event.sticker,),
            )
            self.assertNotIn(
                PROVIDER_FILE_ID,
                repr(list_sticker_model_views(catalog, BOT_NAMESPACE)),
            )

    def test_catalog_id_round_trips_through_sync_kernel_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            event, model_event = self._ingest(catalog)
            plan = _sticker_plan(model_event.sticker.catalog_id)
            reply = action_plan_to_reply(
                plan,
                event.target,
                bot_namespace=BOT_NAMESPACE,
                sticker_catalog=catalog,
            )
            host = SyncStickerHost()

            receipt = InteractionKernel(host, sticker_catalog=catalog).execute_reply(
                reply,
                request_id="sync-sticker",
            )

            self.assertTrue(receipt.completed)
            self.assertTrue(receipt.verified_visible_completion)
            self.assertEqual(receipt.receipts[0].status, DeliveryStatus.VERIFIED)
            self.assertEqual(receipt.receipts[0].request_id, "sync-sticker:0")
            self.assertEqual(
                host.calls,
                [("sync-sticker:0", PROVIDER_FILE_ID, (PROVIDER_FILE_ID,))],
            )
            self.assertNotIn(PROVIDER_FILE_ID, repr(plan))

            direct = InteractionKernel(host, sticker_catalog=catalog).send_catalog_sticker(
                _target(),
                BOT_NAMESPACE,
                model_event.sticker.catalog_id,
                request_id="sync-direct",
            )
            self.assertEqual(direct.status, DeliveryStatus.VERIFIED)
            self.assertEqual(host.calls[-1][0], "sync-direct")

    def test_catalog_id_round_trips_through_async_kernel_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            event, model_event = self._ingest(catalog)
            reply = action_plan_to_reply(
                _sticker_plan(model_event.sticker.catalog_id),
                event.target,
                bot_namespace=BOT_NAMESPACE,
                sticker_catalog=catalog,
            )
            host = AsyncStickerHost()

            async def execute():
                return await AsyncInteractionKernel(
                    host,
                    sticker_catalog=catalog,
                ).execute_reply(reply, request_id="async-sticker")

            receipt = asyncio.run(execute())

            self.assertTrue(receipt.completed)
            self.assertTrue(receipt.verified_visible_completion)
            self.assertEqual(receipt.receipts[0].status, DeliveryStatus.VERIFIED)
            self.assertEqual(receipt.receipts[0].request_id, "async-sticker:0")
            self.assertEqual(
                host.calls,
                [("async-sticker:0", PROVIDER_FILE_ID, (PROVIDER_FILE_ID,))],
            )

    def test_catalog_ids_are_bot_scoped_and_wrong_namespace_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            event = normalize_incoming_sticker(
                _sticker_update(),
                bot_namespace=BOT_NAMESPACE,
            )
            first = catalog.remember(event.sticker)
            second = catalog.remember(
                RegularSticker(
                    bot_namespace=OTHER_BOT_NAMESPACE,
                    file_id="synthetic-other-provider-file",
                    file_unique_id=PROVIDER_UNIQUE_ID,
                    emoji=first.emoji,
                    set_name=first.set_name,
                    format=first.format,
                    thumbnail_ref=first.thumbnail_ref,
                    media_ref=first.media_ref,
                )
            )
            self.assertNotEqual(first.catalog_id, second.catalog_id)
            self.assertIsNone(catalog.resolve(OTHER_BOT_NAMESPACE, first.catalog_id))
            self.assertEqual(
                tuple(
                    view.catalog_id
                    for view in list_sticker_model_views(
                        catalog,
                        OTHER_BOT_NAMESPACE,
                    )
                ),
                (second.catalog_id,),
            )

            with self.assertRaises(ActionPlanError):
                action_plan_to_reply(
                    _sticker_plan(first.catalog_id),
                    _target(),
                    bot_namespace=OTHER_BOT_NAMESPACE,
                    sticker_catalog=catalog,
                )

            host = SyncStickerHost()
            receipt = InteractionKernel(host, sticker_catalog=catalog).send_catalog_sticker(
                _target(),
                OTHER_BOT_NAMESPACE,
                first.catalog_id,
                request_id="wrong-namespace",
            )
            self.assertEqual(receipt.status, DeliveryStatus.FAILED)
            self.assertEqual(receipt.detail, "sticker_catalog_id_unknown")
            self.assertEqual(host.calls, [])

    def test_mask_and_custom_emoji_are_rejected_before_cataloging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            for sticker_type in (StickerType.MASK, StickerType.CUSTOM_EMOJI):
                with self.subTest(sticker_type=sticker_type):
                    event = normalize_incoming_sticker(
                        _sticker_update(sticker_type=sticker_type.value),
                        bot_namespace=BOT_NAMESPACE,
                    )
                    with self.assertRaises(ValueError):
                        ingest_incoming_sticker(event, catalog)
                    self.assertEqual(catalog.list(BOT_NAMESPACE), ())


if __name__ == "__main__":
    unittest.main()
