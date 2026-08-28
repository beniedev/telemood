from __future__ import annotations

import unittest

import telemood
from telemood import (
    AsyncInjectedTelegramAdapter,
    AsyncInteractionKernel,
    BubbleRequest,
    CallbackRegistry,
    CallbackToken,
    ChoiceOption,
    ChoicesRequest,
    DeliveryStatus,
    InjectedResult,
    InjectedTelegramAdapter,
    InteractionCapabilities,
    InteractionKernel,
    ReactionRequest,
    ReactionType,
    RichReply,
    StickerRequest,
    TargetRef,
    check_adapter,
    normalize_callback_query,
    normalize_incoming_reaction_change,
    normalize_incoming_reaction_count,
    normalize_incoming_sticker,
)


def _target() -> TargetRef:
    return TargetRef("telegram", "100", "200", "300")


class FakeInjectedClient:
    def __init__(self) -> None:
        self.calls = []
        self.results = {
            "message": InjectedResult(True, "message-ok"),
            "reaction": InjectedResult(True, "reaction-ok"),
            "sticker": InjectedResult(True, "sticker-ok"),
            "choices": InjectedResult(True, "choices-ok"),
        }

    def _result(self, name, kwargs):
        self.calls.append((name, kwargs))
        result = self.results[name]
        if isinstance(result, BaseException):
            raise result
        return result

    def send_message(self, **kwargs):
        return self._result("message", kwargs)

    def set_reaction(self, **kwargs):
        return self._result("reaction", kwargs)

    def send_sticker(self, **kwargs):
        return self._result("sticker", kwargs)

    def send_choices(self, **kwargs):
        return self._result("choices", kwargs)


class AdapterConformanceTests(unittest.TestCase):
    def test_outbound_mapping_is_explicit_and_stops_sticker_sequence(self) -> None:
        client = FakeInjectedClient()
        adapter = InjectedTelegramAdapter(client)
        self.assertTrue(check_adapter(adapter).ok)

        bubble = InteractionKernel(adapter).send_bubble(
            BubbleRequest(_target(), "hello"),
            request_id="bubble",
        )
        self.assertEqual(bubble.status, DeliveryStatus.VERIFIED)
        self.assertEqual(bubble.provider_delivery_id, "message-ok")

        client.results["sticker"] = InjectedResult(False, detail="provider-rejected")
        sticker = InteractionKernel(adapter).send_sticker(
            StickerRequest(_target(), "sticker-ref", "before", "after"),
            request_id="sticker",
        )
        self.assertEqual(sticker.status, DeliveryStatus.FAILED)
        self.assertEqual(sticker.detail, "provider-rejected")
        self.assertEqual(sticker.unexecuted_parts, 1)
        self.assertEqual([name for name, _ in client.calls[-2:]], ["message", "sticker"])

        client.results["message"] = object()
        unknown = InteractionKernel(adapter).send_bubble(
            BubbleRequest(_target(), "unknown"),
            request_id="invalid",
        )
        self.assertEqual(unknown.status, DeliveryStatus.UNKNOWN)

        client.results["message"] = TimeoutError()
        uncertain = InteractionKernel(adapter).send_bubble(
            BubbleRequest(_target(), "timeout"),
            request_id="timeout",
        )
        self.assertEqual(uncertain.status, DeliveryStatus.UNCERTAIN)
        self.assertEqual(uncertain.detail, "transport_timeout")

    def test_choices_receive_only_callback_handles(self) -> None:
        client = FakeInjectedClient()
        adapter = InjectedTelegramAdapter(client)
        request = ChoicesRequest(
            _target(),
            "Choose",
            (ChoiceOption("yes", "Yes"), ChoiceOption("no", "No")),
            "user-1",
        )
        tokens = {"yes": CallbackToken("token-a"), "no": CallbackToken("token-b")}

        receipt = adapter.send_choices("choices", request, tokens)

        self.assertEqual(receipt.status, DeliveryStatus.VERIFIED)
        self.assertEqual(
            client.calls[-1][1]["options"],
            (("Yes", "token-a"), ("No", "token-b")),
        )

    def test_bot_api_update_normalizers_cover_sticker_reactions_and_callback(self) -> None:
        sticker = normalize_incoming_sticker(
            {
                "message": {
                    "message_id": 20,
                    "message_thread_id": 30,
                    "date": 1000,
                    "chat": {"id": -10},
                    "from": {"id": 40},
                    "sticker": {
                        "file_id": "provider-file",
                        "file_unique_id": "provider-unique",
                        "type": "regular",
                        "is_animated": True,
                        "is_video": False,
                        "emoji": "🙂",
                        "set_name": "synthetic-set",
                    },
                }
            },
            bot_namespace="bot-a",
            media_ref="media/sticker-a",
        )
        self.assertEqual(sticker.target.thread_id, "30")
        self.assertEqual(sticker.sender_user_id, "40")
        self.assertEqual(sticker.sticker.format.value, "animated")

        change = normalize_incoming_reaction_change(
            {
                "message_reaction": {
                    "chat": {"id": -10},
                    "message_id": 20,
                    "user": {"id": 40},
                    "date": 1001,
                    "old_reaction": [{"type": "emoji", "emoji": "👍"}],
                    "new_reaction": [
                        {"type": "custom_emoji", "custom_emoji_id": "custom-a"}
                    ],
                }
            }
        )
        self.assertEqual(change.actor.user_id, "40")
        self.assertEqual(change.old_reactions[0].type, ReactionType.EMOJI)
        self.assertEqual(change.new_reactions[0].type, ReactionType.CUSTOM_EMOJI)

        count = normalize_incoming_reaction_count(
            {
                "message_reaction_count": {
                    "chat": {"id": -10},
                    "message_id": 20,
                    "date": 1002,
                    "reactions": [
                        {"type": {"type": "emoji", "emoji": "👍"}, "total_count": 3}
                    ],
                }
            }
        )
        self.assertEqual(count.counts[0].total_count, 3)
        self.assertTrue(count.delayed)

        callback = normalize_callback_query(
            {
                "callback_query": {
                    "from": {"id": 40},
                    "data": "callback-token",
                    "message": {
                        "message_id": 20,
                        "message_thread_id": 30,
                        "chat": {"id": -10},
                    },
                }
            }
        )
        self.assertEqual(callback.token.value, "callback-token")
        self.assertEqual(callback.target.chat_id, "-10")

    def test_v01_top_level_has_no_miniapp_surface(self) -> None:
        self.assertFalse(hasattr(telemood, "MiniAppRequest"))
        self.assertFalse(hasattr(telemood.InteractionKernel, "send_miniapp"))


class AsyncFakeInjectedClient(FakeInjectedClient):
    async def send_message(self, **kwargs):
        return self._result("message", kwargs)

    async def set_reaction(self, **kwargs):
        return self._result("reaction", kwargs)

    async def send_sticker(self, **kwargs):
        return self._result("sticker", kwargs)

    async def send_choices(self, **kwargs):
        return self._result("choices", kwargs)


class AsyncAdapterConformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_choices_expose_the_same_expiry_metadata(self) -> None:
        client = AsyncFakeInjectedClient()
        clock = lambda: 100.0
        kernel = AsyncInteractionKernel(
            AsyncInjectedTelegramAdapter(client),
            callbacks=CallbackRegistry(
                clock=clock,
                deadline_clock=clock,
                token_factory=iter(("token-a", "token-b")).__next__,
            ),
        )
        request = ChoicesRequest(
            _target(),
            "Choose",
            (ChoiceOption("yes", "Yes"), ChoiceOption("no", "No")),
            "user-1",
            callback_ttl_seconds=5.0,
        )

        receipt = await kernel.send_choices(request, request_id="async-choices")

        self.assertEqual(receipt.callback_expires_at, 105.0)

    async def test_async_kernel_awaits_host_and_preserves_order(self) -> None:
        client = AsyncFakeInjectedClient()
        adapter = AsyncInjectedTelegramAdapter(client)
        self.assertTrue(check_adapter(adapter, mode="async").ok)
        kernel = AsyncInteractionKernel(adapter)
        reply = RichReply(
            (
                BubbleRequest(_target(), "first"),
                ReactionRequest(_target(), "👍"),
                BubbleRequest(_target(), "last"),
            )
        )

        receipt = await kernel.execute_reply(
            reply,
            request_id="async",
            capabilities=InteractionCapabilities(can_send_reactions=True),
        )

        self.assertTrue(receipt.completed)
        self.assertEqual([name for name, _ in client.calls], ["message", "reaction", "message"])


if __name__ == "__main__":
    unittest.main()
