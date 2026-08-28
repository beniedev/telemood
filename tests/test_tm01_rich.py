from __future__ import annotations

import multiprocessing
import tempfile
import threading
import unittest
from itertools import count
from pathlib import Path

from telemood import (
    BubbleRequest,
    CallbackPayload,
    CallbackRejection,
    CallbackToken,
    ChoiceOption,
    ChoicesRequest,
    DeliveryStatus,
    IncomingReactionChange,
    IncomingSticker,
    InteractionCapabilities,
    InteractionKind,
    InteractionKernel,
    InteractionReceipt,
    ReactionActor,
    ReactionRejection,
    ReactionRequest,
    ReactionType,
    ReactionValue,
    RegularSticker,
    RichReply,
    SQLiteCallbackStore,
    SQLiteStickerCatalog,
    StickerFormat,
    StickerRequest,
    TargetRef,
    TransportReceipt,
    split_semantic_bubbles,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RichHost:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.reaction_result = TransportReceipt(DeliveryStatus.VERIFIED, "reaction")
        self.bubble_result = TransportReceipt(DeliveryStatus.VERIFIED, "bubble")
        self.choices_result = TransportReceipt(DeliveryStatus.VERIFIED, "choices")
        self.sticker_result: list[TransportReceipt] = [
            TransportReceipt(DeliveryStatus.VERIFIED, "sticker")
        ]

    def send_bubble(self, request_id, request):
        self.events.append(("bubble", request_id))
        return self.bubble_result

    def send_reaction(self, request_id, request):
        self.events.append(("reaction", request_id))
        return self.reaction_result

    def send_sticker_sequence(self, request_id, request, parts):
        self.events.append(("sticker", request_id))
        return self.sticker_result

    def send_choices(self, request_id, request, callback_tokens):
        self.events.append(("choices", request_id))
        return self.choices_result


def _consume_in_process(path: str, token_value: str, results) -> None:
    store = SQLiteCallbackStore(path)
    resolution = store.consume(
        CallbackToken(token_value),
        user_id="user",
        chat_id="chat",
        thread_id="thread-a",
    )
    results.put(
        (resolution.accepted, resolution.reason.value if resolution.reason else None)
    )


def target(message_id: str | None = "message") -> TargetRef:
    return TargetRef(channel="synthetic", chat_id="chat", message_id=message_id)


class RichInteractionTests(unittest.TestCase):
    def test_sequence_is_ordered_and_stops_at_first_non_verified(self) -> None:
        host = RichHost()
        kernel = InteractionKernel(host)
        reply = RichReply(
            (
                BubbleRequest(target(), "first"),
                ReactionRequest(target(), "👍"),
                StickerRequest(target(), "sticker-id"),
                BubbleRequest(target(), "never"),
            )
        )
        host.reaction_result = TransportReceipt(DeliveryStatus.UNCERTAIN)

        reaction_capabilities = InteractionCapabilities(can_send_reactions=True)
        stopped = kernel.execute_reply(
            reply,
            request_id="reply",
            capabilities=reaction_capabilities,
        )

        self.assertFalse(stopped.completed)
        self.assertEqual(stopped.stopped_at, 1)
        self.assertEqual(stopped.unexecuted_count, 2)
        self.assertEqual(
            host.events,
            [("bubble", "reply:0"), ("reaction", "reply:1")],
        )

        host.reaction_result = TransportReceipt(DeliveryStatus.VERIFIED, "reaction")
        completed = kernel.execute_reply(
            reply,
            request_id="reply",
            capabilities=reaction_capabilities,
        )
        self.assertTrue(completed.completed)
        self.assertIsNone(completed.stopped_at)
        self.assertEqual(
            [event[0] for event in host.events[2:]],
            ["bubble", "reaction", "sticker", "bubble"],
        )
        self.assertEqual(
            [receipt.request_id for receipt in completed.receipts],
            ["reply:0", "reply:1", "reply:2", "reply:3"],
        )

    def test_bubble_split_preserves_body_and_limit(self) -> None:
        text = "First paragraph.\n\n" + ("word " * 900)
        chunks = split_semantic_bubbles(text, max_length=100)
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 100 for chunk in chunks))
        self.assertEqual("".join(chunks).strip(), text.strip())
        prioritized = split_semantic_bubbles(
            "First.\n\nSecond sentence. Tail",
            max_length=25,
        )
        self.assertEqual(prioritized[0], "First.\n\n")
        sentence_first = split_semantic_bubbles(
            "One sentence. two words plus",
            max_length=22,
        )
        self.assertEqual(sentence_first[0], "One sentence.")
        self.assertEqual(
            split_semantic_bubbles("xxxxxxxxx", max_length=4),
            ("xxxx", "xxxx", "x"),
        )
        with self.assertRaises(ValueError):
            BubbleRequest(target(), "x" * 4097)

    def test_reaction_target_capability_and_bot_generated_input(self) -> None:
        with self.assertRaises(ValueError):
            ReactionRequest(target(None), "👍")

        host = RichHost()
        kernel = InteractionKernel(host)
        receipt = kernel.send_reaction(
            ReactionRequest(target(), "👍"),
            request_id="reaction",
            capabilities=InteractionCapabilities(can_send_reactions=False),
        )
        self.assertEqual(receipt.status, DeliveryStatus.FAILED)
        self.assertEqual(host.events, [])

        stopped = kernel.execute_reply(
            RichReply(
                (ReactionRequest(target(), "👍"), BubbleRequest(target(), "never"))
            ),
            request_id="reply-capability",
            capabilities=InteractionCapabilities(can_send_reactions=False),
        )
        self.assertEqual(stopped.stopped_at, 0)
        self.assertEqual(host.events, [])

        unavailable = kernel.send_reaction(
            ReactionRequest(target(), "👀"),
            request_id="reaction-not-allowed",
            capabilities=InteractionCapabilities(
                can_send_reactions=True,
                available_reactions=("👍",),
            ),
        )
        self.assertEqual(unavailable.status, DeliveryStatus.FAILED)
        allowed = kernel.send_reaction(
            ReactionRequest(target(), "👍"),
            request_id="reaction-allowed",
            capabilities=InteractionCapabilities(
                can_send_reactions=True,
                available_reactions=("👍",),
            ),
        )
        self.assertEqual(allowed.status, DeliveryStatus.VERIFIED)
        self.assertEqual(host.events, [("reaction", "reaction-allowed")])

        capabilities = InteractionCapabilities(
            can_receive_reaction_changes=True,
            message_reaction_subscribed=True,
        )
        user_event = IncomingReactionChange(
            target(),
            ReactionActor(user_id="user"),
            (),
            (ReactionValue(ReactionType.EMOJI, "👍"),),
            100,
        )
        accepted = kernel.accept_incoming_reaction(user_event, capabilities)
        self.assertTrue(accepted.accepted)
        self.assertIs(accepted.event, user_event)

        bot_event = IncomingReactionChange(
            target(),
            ReactionActor(user_id="bot"),
            (),
            (ReactionValue(ReactionType.EMOJI, "👍"),),
            101,
            bot_generated=True,
        )
        self.assertEqual(
            kernel.accept_incoming_reaction(bot_event, capabilities).reason,
            ReactionRejection.BOT_GENERATED,
        )
        not_subscribed = InteractionCapabilities(can_receive_reaction_changes=True)
        self.assertEqual(
            kernel.accept_incoming_reaction(user_event, not_subscribed).reason,
            ReactionRejection.UPDATES_NOT_SUBSCRIBED,
        )

    def test_sticker_catalog_is_namespace_scoped_and_send_seen_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stickers.sqlite3"
            catalog = SQLiteStickerCatalog(path)
            first = IncomingSticker(
                bot_namespace="bot-a",
                file_id="file-a",
                file_unique_id="unique",
                emoji="🙂",
                set_name="synthetic-set",
                format=StickerFormat.STATIC,
            )
            second = RegularSticker(
                bot_namespace="bot-b",
                file_id="file-b",
                file_unique_id="unique",
                format=StickerFormat.VIDEO,
            )
            catalog.remember(first)
            catalog.remember(second)
            self.assertEqual(catalog.get("bot-a", "unique").file_id, "file-a")
            self.assertEqual(catalog.get("bot-b", "unique").file_id, "file-b")

            host = RichHost()
            kernel = InteractionKernel(host, sticker_catalog=catalog)
            sent = kernel.send_seen_sticker(target(), "bot-a", "unique", request_id="seen")
            self.assertEqual(sent.status, DeliveryStatus.VERIFIED)
            self.assertEqual(host.events[-1], ("sticker", "seen"))
            self.assertEqual(host.sticker_result[0].provider_delivery_id, "sticker")

            missing = kernel.send_seen_sticker(
                target(), "bot-c", "unique", request_id="missing"
            )
            self.assertEqual(missing.status, DeliveryStatus.FAILED)
            self.assertEqual(missing.detail, "sticker_not_seen_in_bot_namespace")


class SQLiteCallbackTests(unittest.TestCase):
    def test_separate_processes_consume_at_most_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "callbacks-process.sqlite3")
            store = SQLiteCallbackStore(path)
            token = store.register(
                user_id="user",
                chat_id="chat",
                thread_id="thread-a",
                payload=CallbackPayload(InteractionKind.CHOICES, "reply", "yes"),
                ttl_seconds=30,
            )
            self.assertTrue(store.activate(token))

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            processes = [
                context.Process(
                    target=_consume_in_process,
                    args=(path, token.value, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)

            outcomes = [results.get(timeout=2) for _ in processes]
            self.assertEqual(sum(accepted for accepted, _ in outcomes), 1)
            self.assertEqual(sum(reason == "replay" for _, reason in outcomes), 1)

    def test_two_store_instances_consume_one_shot_and_bind_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "callbacks.sqlite3"
            clock = FakeClock()
            sequence = count()

            def token_factory() -> str:
                return f"synthetic-{next(sequence)}"

            first = SQLiteCallbackStore(path, clock=clock, token_factory=token_factory)
            second = SQLiteCallbackStore(path, clock=clock, token_factory=token_factory)
            token = first.register(
                user_id="user",
                chat_id="chat",
                thread_id="thread-a",
                payload=CallbackPayload(InteractionKind.CHOICES, "reply", "yes"),
                ttl_seconds=10,
            )
            self.assertEqual(token.expires_at, 110.0)
            self.assertTrue(first.activate(token))

            barrier = threading.Barrier(2)
            results = []

            def consume(store: SQLiteCallbackStore) -> None:
                barrier.wait()
                results.append(
                    store.consume(
                        token,
                        user_id="user",
                        chat_id="chat",
                        thread_id="thread-a",
                    )
                )

            threads = [
                threading.Thread(target=consume, args=(first,)),
                threading.Thread(target=consume, args=(second,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(sum(result.accepted for result in results), 1)
            self.assertEqual(
                sum(result.reason is CallbackRejection.REPLAY for result in results),
                1,
            )

            thread_token = first.register(
                user_id="user",
                chat_id="chat",
                thread_id="thread-a",
                payload=CallbackPayload(InteractionKind.CHOICES, "reply-2", "no"),
                ttl_seconds=10,
            )
            self.assertTrue(first.activate(thread_token))
            wrong_thread = second.consume(
                thread_token,
                user_id="user",
                chat_id="chat",
                thread_id="thread-b",
            )
            self.assertEqual(wrong_thread.reason, CallbackRejection.THREAD_MISMATCH)

            expiring = first.register(
                user_id="user",
                chat_id="chat",
                payload=CallbackPayload(InteractionKind.CHOICES, "reply-3", "yes"),
                ttl_seconds=2,
            )
            self.assertTrue(first.activate(expiring))
            clock.advance(2)
            expired = second.consume(expiring, user_id="user", chat_id="chat")
            self.assertEqual(expired.reason, CallbackRejection.EXPIRED)

    def test_kernel_binds_choice_callbacks_to_target_thread(self) -> None:
        host = RichHost()
        kernel = InteractionKernel(host)
        request = ChoicesRequest(
            target=TargetRef(
                channel="synthetic",
                chat_id="chat",
                thread_id="thread",
            ),
            prompt="Choose",
            options=(ChoiceOption("yes", "Yes"), ChoiceOption("no", "No")),
            authorized_user_id="user",
        )
        receipt = kernel.send_choices(request, request_id="choices-thread")
        token = receipt.callback_tokens[0]
        wrong = kernel.consume_callback(
            token,
            user_id="user",
            chat_id="chat",
            thread_id="other",
        )
        self.assertEqual(wrong.reason, CallbackRejection.THREAD_MISMATCH)
        right = kernel.consume_callback(
            token,
            user_id="user",
            chat_id="chat",
            thread_id="thread",
        )
        self.assertTrue(right.accepted)


if __name__ == "__main__":
    unittest.main()
