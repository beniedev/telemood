from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from telemood import (
    PLAN_VERSION,
    CallbackToken,
    DeliveryStatus,
    IncomingReactionChange,
    IncomingReactionCount,
    IncomingSticker,
    IncomingStickerEvent,
    InteractionCapabilities,
    InteractionKernel,
    ReactionActor,
    ReactionCount,
    ReactionRejection,
    ReactionRequest,
    ReactionType,
    ReactionValue,
    SQLiteStickerCatalog,
    StickerFormat,
    StickerType,
    TargetRef,
    TransportReceipt,
    action_plan_to_reply,
    ingest_incoming_sticker,
    split_semantic_bubbles,
)


def _target() -> TargetRef:
    return TargetRef(
        channel="telegram",
        chat_id="synthetic-chat",
        message_id="synthetic-message",
        thread_id="synthetic-thread",
    )


class OrderedHost:
    def __init__(self, reaction_status: DeliveryStatus = DeliveryStatus.VERIFIED) -> None:
        self.events: list[tuple[str, str]] = []
        self.reaction_status = reaction_status

    def send_bubble(self, request_id, request):  # noqa: ARG002
        self.events.append(("bubble", request_id))
        return TransportReceipt(DeliveryStatus.VERIFIED, f"provider-{request_id}")

    def send_reaction(self, request_id, request):  # noqa: ARG002
        self.events.append(("reaction", request_id))
        return TransportReceipt(self.reaction_status, f"provider-{request_id}")

    def send_sticker_sequence(self, request_id, request, parts):  # noqa: ARG002
        self.events.append(("sticker", request_id))
        return tuple(
            TransportReceipt(DeliveryStatus.VERIFIED, f"provider-{request_id}-{index}")
            for index, _part in enumerate(parts)
        )

    def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
        self.events.append(("choices", request_id))
        return TransportReceipt(DeliveryStatus.VERIFIED, f"provider-{request_id}")


class PlanExecutionTests(unittest.TestCase):
    def _bound_reply(self, catalog: SQLiteStickerCatalog):
        stored = catalog.remember(
            IncomingSticker(
                bot_namespace="bot-a",
                file_id="provider-sticker-file",
                file_unique_id="provider-sticker-unique",
            )
        )
        plan = {
            "version": PLAN_VERSION,
            "actions": [
                {"type": "bubble", "text": "first"},
                {"type": "reaction", "target": "trigger_message", "emoji": "👍"},
                {
                    "type": "sticker",
                    "sticker": {"kind": "catalog", "id": stored.catalog_id},
                },
                {"type": "bubble", "text": "second"},
                {
                    "type": "choices",
                    "prompt": "Choose",
                    "options": [
                        {"key": "yes", "label": "Yes"},
                        {"key": "no", "label": "No"},
                    ],
                },
                {
                    "type": "sticker",
                    "sticker": {"kind": "catalog", "id": stored.catalog_id},
                },
            ],
        }
        return action_plan_to_reply(
            plan,
            _target(),
            authorized_user_id="trusted-user",
            bot_namespace="bot-a",
            sticker_catalog=catalog,
        )

    def test_six_actions_execute_in_exact_plan_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            reply = self._bound_reply(catalog)
            host = OrderedHost()
            receipt = InteractionKernel(host).execute_reply(
                reply,
                request_id="plan",
                capabilities=InteractionCapabilities(can_send_reactions=True),
            )

        self.assertTrue(receipt.completed)
        self.assertEqual(receipt.total_actions, 6)
        self.assertEqual(receipt.unexecuted_count, 0)
        self.assertEqual(
            [kind for kind, _request_id in host.events],
            ["bubble", "reaction", "sticker", "bubble", "choices", "sticker"],
        )
        self.assertEqual(
            [item.request_id for item in receipt.receipts],
            [f"plan:{index}" for index in range(6)],
        )

    def test_each_non_verified_status_stops_after_second_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            reply = self._bound_reply(catalog)
            for status in (
                DeliveryStatus.FAILED,
                DeliveryStatus.UNKNOWN,
                DeliveryStatus.UNCERTAIN,
            ):
                with self.subTest(status=status):
                    host = OrderedHost(status)
                    receipt = InteractionKernel(host).execute_reply(
                        reply,
                        request_id=f"plan-{status.value}",
                        capabilities=InteractionCapabilities(can_send_reactions=True),
                    )
                    self.assertFalse(receipt.completed)
                    self.assertEqual(receipt.stopped_at, 1)
                    self.assertEqual(receipt.unexecuted_count, 4)
                    self.assertEqual([kind for kind, _ in host.events], ["bubble", "reaction"])


class StickerInboundTests(unittest.TestCase):
    def test_regular_formats_ingest_and_return_safe_model_views(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            for sticker_format in StickerFormat:
                with self.subTest(sticker_format=sticker_format):
                    raw_file_id = f"provider-file-{sticker_format.value}"
                    view = ingest_incoming_sticker(
                        IncomingStickerEvent(
                            target=_target(),
                            sticker=IncomingSticker(
                                bot_namespace="bot-a",
                                file_id=raw_file_id,
                                file_unique_id=f"provider-unique-{sticker_format.value}",
                                emoji="🙂",
                                set_name="synthetic-set",
                                format=sticker_format,
                            ),
                            received_at=100,
                            sender_user_id="synthetic-user",
                        ),
                        catalog,
                    )
                    self.assertTrue(view.sticker.catalog_id.startswith("sticker_"))
                    self.assertIn(sticker_format.value, view.sticker.text)
                    self.assertIn("image content not attached", view.sticker.text)
                    self.assertEqual(view.sender_kind.value, "user")
                    self.assertEqual(view.target_role, "telegram")
                    self.assertTrue(view.in_thread)
                    self.assertEqual(view.occurred_at, 100)
                    self.assertNotIn(raw_file_id, repr(view))
            self.assertEqual(len(catalog.list("bot-a")), 3)

    def test_non_regular_stickers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            for sticker_type in (StickerType.MASK, StickerType.CUSTOM_EMOJI):
                with self.subTest(sticker_type=sticker_type), self.assertRaises(ValueError):
                    ingest_incoming_sticker(
                        IncomingStickerEvent(
                            target=_target(),
                            sticker=IncomingSticker(
                                bot_namespace="bot-a",
                                file_id=f"provider-{sticker_type.value}",
                                file_unique_id=f"unique-{sticker_type.value}",
                                type=sticker_type,
                            ),
                            received_at=100,
                            sender_chat_id="synthetic-sender-chat",
                        ),
                        catalog,
                    )
            self.assertEqual(catalog.list("bot-a"), ())

    def test_pre_catalog_id_database_is_migrated_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stickers.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE stickers (
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
                connection.execute(
                    """
                    INSERT INTO stickers (
                        bot_namespace, file_unique_id, file_id, sticker_format
                    ) VALUES (?, ?, ?, ?)
                    """,
                    ("bot-a", "old-unique", "old-file", "static"),
                )
                connection.commit()

            migrated = SQLiteStickerCatalog(path).get("bot-a", "old-unique")

            self.assertEqual(migrated.file_id, "old-file")
            self.assertTrue(migrated.catalog_id.startswith("sticker_"))


class ReactionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = InteractionCapabilities(
            can_receive_reaction_changes=True,
            can_receive_reaction_counts=True,
            message_reaction_subscribed=True,
            message_reaction_count_subscribed=True,
        )

    def test_change_preserves_old_new_sets_and_actor_chat(self) -> None:
        old = ReactionValue(ReactionType.EMOJI, "👍")
        new = ReactionValue(ReactionType.EMOJI, "❤️")
        event = IncomingReactionChange(
            target=_target(),
            actor=ReactionActor(chat_id="synthetic-actor-chat"),
            old_reactions=(old,),
            new_reactions=(new,),
            changed_at=100,
        )

        accepted = InteractionKernel.accept_incoming_reaction(event, self.capabilities)

        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.event.old_reactions, (old,))
        self.assertEqual(accepted.event.new_reactions, (new,))

    def test_removal_and_anonymous_delayed_count_are_distinct(self) -> None:
        removal = IncomingReactionChange(
            _target(),
            ReactionActor(user_id="synthetic-user"),
            (ReactionValue(ReactionType.EMOJI, "👍"),),
            (),
            101,
        )
        count = IncomingReactionCount(
            _target(),
            (ReactionCount(ReactionValue(ReactionType.EMOJI, "👍"), 3),),
            102,
        )

        self.assertTrue(
            InteractionKernel.accept_incoming_reaction(removal, self.capabilities).accepted
        )
        accepted_count = InteractionKernel.accept_incoming_reaction(
            count,
            self.capabilities,
        )
        self.assertTrue(accepted_count.accepted)
        self.assertTrue(accepted_count.event.delayed)

        change_only = InteractionCapabilities(
            can_receive_reaction_changes=True,
            can_receive_reaction_counts=True,
            message_reaction_subscribed=True,
            reaction_count_unavailable_reason="count updates not requested",
        )
        self.assertTrue(
            InteractionKernel.accept_incoming_reaction(removal, change_only).accepted
        )
        rejected_count = InteractionKernel.accept_incoming_reaction(count, change_only)
        self.assertEqual(rejected_count.reason, ReactionRejection.UPDATES_NOT_SUBSCRIBED)
        self.assertEqual(rejected_count.detail, "count updates not requested")

    def test_unsupported_and_unavailable_reasons_are_explicit(self) -> None:
        for value in (
            ReactionValue(ReactionType.CUSTOM_EMOJI, "custom-id"),
            ReactionValue(ReactionType.PAID),
        ):
            event = IncomingReactionChange(
                _target(),
                ReactionActor(user_id="synthetic-user"),
                (),
                (value,),
                103,
            )
            self.assertEqual(
                InteractionKernel.accept_incoming_reaction(event, self.capabilities).reason,
                ReactionRejection.UNSUPPORTED_REACTION_TYPE,
            )

        ordinary = IncomingReactionChange(
            _target(),
            ReactionActor(user_id="synthetic-user"),
            (),
            (ReactionValue(ReactionType.EMOJI, "👍"),),
            104,
        )
        self.assertEqual(
            InteractionKernel.accept_incoming_reaction(
                ordinary,
                InteractionCapabilities(),
            ).reason,
            ReactionRejection.CAPABILITY_UNAVAILABLE,
        )
        self.assertEqual(
            InteractionKernel.accept_incoming_reaction(
                ordinary,
                InteractionCapabilities(can_receive_reaction_changes=True),
            ).reason,
            ReactionRejection.UPDATES_NOT_SUBSCRIBED,
        )

    def test_reaction_send_defaults_to_fail_closed(self) -> None:
        host = OrderedHost()
        receipt = InteractionKernel(host).send_reaction(
            ReactionRequest(_target(), "👍"),
            request_id="reaction",
        )
        self.assertEqual(receipt.status, DeliveryStatus.FAILED)
        self.assertEqual(host.events, [])


class BoundaryTests(unittest.TestCase):
    def test_semantic_splitter_covers_empty_exact_overflow_and_chinese(self) -> None:
        self.assertEqual(split_semantic_bubbles(""), ())
        self.assertEqual(split_semantic_bubbles("x" * 10, 10), ("x" * 10,))
        self.assertEqual(split_semantic_bubbles("x" * 11, 10), ("x" * 10, "x"))
        self.assertEqual(split_semantic_bubbles("第一句。第二句。", 4), ("第一句。", "第二句。"))

    def test_callback_token_limit_is_utf8_bytes(self) -> None:
        self.assertEqual(CallbackToken("x" * 64).value, "x" * 64)
        with self.assertRaises(ValueError):
            CallbackToken("é" * 33)


if __name__ == "__main__":
    unittest.main()
