from __future__ import annotations

import unittest

from telemood import (
    CallbackRejection,
    ChoiceOption,
    ChoicesRequest,
    CompletionMode,
    DeliveryStatus,
    InteractionCapabilities,
    InteractionKernel,
    ReactionRequest,
    StickerRequest,
    TargetRef,
    TransportReceipt,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeHost:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.reaction_result: object = TransportReceipt(DeliveryStatus.VERIFIED, "reaction-1")
        self.choices_result = TransportReceipt(DeliveryStatus.VERIFIED, "choices-1")
        self.sticker_result: object = [TransportReceipt(DeliveryStatus.VERIFIED, "sticker-1")]
        self.choice_tokens = {}
        self.reaction_request_ids = []
        self.choices_request_ids = []
        self.sticker_request_ids = []
        self.advance_choices_before_return = 0.0

    def send_reaction(self, request_id, request):
        self.reaction_request_ids.append(request_id)
        if isinstance(self.reaction_result, BaseException):
            raise self.reaction_result
        return self.reaction_result

    def send_choices(self, request_id, request, callback_tokens):
        self.choices_request_ids.append(request_id)
        self.choice_tokens = dict(callback_tokens)
        self.clock.advance(self.advance_choices_before_return)
        if isinstance(self.choices_result, BaseException):
            raise self.choices_result
        return self.choices_result

    def send_sticker_sequence(self, request_id, request, parts):
        self.sticker_request_ids.append(request_id)
        if isinstance(self.sticker_result, BaseException):
            raise self.sticker_result
        return self.sticker_result


def target() -> TargetRef:
    return TargetRef(channel="synthetic", chat_id="chat-1", message_id="message-1")


def choices_request(ttl: float = 10.0) -> ChoicesRequest:
    return ChoicesRequest(
        target=target(),
        prompt="Choose one",
        options=(ChoiceOption("yes", "Yes"), ChoiceOption("no", "No")),
        authorized_user_id="user-1",
        callback_ttl_seconds=ttl,
    )


class InteractionKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.host = FakeHost(self.clock)
        self.ids = iter(f"request-{index}" for index in range(1, 20))
        self.token_index = 0
        from telemood import CallbackRegistry

        self.registry = CallbackRegistry(
            clock=self.clock,
            deadline_clock=self.clock,
            token_factory=self._token,
        )
        self.kernel = InteractionKernel(
            self.host,
            callbacks=self.registry,
            request_id_factory=lambda: next(self.ids),
        )
        self.reaction_capabilities = InteractionCapabilities(can_send_reactions=True)

    def _token(self) -> str:
        self.token_index += 1
        return f"callback-{self.token_index}-{self.clock.value}"

    def test_reaction_is_explicitly_nonblocking(self) -> None:
        receipt = self.kernel.send_reaction(
            ReactionRequest(target(), "👍"),
            request_id="reaction-request",
            capabilities=self.reaction_capabilities,
        )

        self.assertEqual(receipt.status, DeliveryStatus.VERIFIED)
        self.assertEqual(receipt.completion_mode, CompletionMode.NONBLOCKING)
        self.assertFalse(receipt.verified_visible_completion)
        self.assertEqual(self.host.reaction_request_ids, ["reaction-request"])

        self.host.reaction_result = TransportReceipt(DeliveryStatus.UNCERTAIN)
        uncertain = self.kernel.send_reaction(
            ReactionRequest(target(), "👀"),
            request_id="reaction-uncertain",
            capabilities=self.reaction_capabilities,
        )
        self.assertEqual(uncertain.status, DeliveryStatus.UNCERTAIN)
        self.assertFalse(uncertain.verified_visible_completion)
        self.assertEqual(
            self.host.reaction_request_ids,
            ["reaction-request", "reaction-uncertain"],
        )

    def test_unverified_choices_revoke_callbacks_and_never_complete(self) -> None:
        for status in (DeliveryStatus.UNKNOWN, DeliveryStatus.FAILED, DeliveryStatus.UNCERTAIN):
            self.host.choices_result = TransportReceipt(status)
            receipt = self.kernel.send_choices(
                choices_request(), request_id=f"choices-{status.value}"
            )

            self.assertEqual(receipt.status, status)
            self.assertFalse(receipt.verified_visible_completion)
            self.assertEqual(receipt.callback_tokens, ())
            self.assertIsNone(receipt.callback_expires_at)
            self.assertEqual(self.host.choices_request_ids[-1], receipt.request_id)
            token = next(iter(self.host.choice_tokens.values()))
            resolution = self.kernel.consume_callback(
                token,
                user_id="user-1",
                chat_id="chat-1",
            )
            self.assertFalse(resolution.accepted)
            self.assertEqual(resolution.reason, CallbackRejection.REVOKED)

    def test_choices_bind_user_and_chat_and_are_one_shot(self) -> None:
        receipt = self.kernel.send_choices(choices_request(), request_id="choices-request")
        token = receipt.callback_tokens[0]
        self.assertEqual(self.host.choices_request_ids, ["choices-request"])
        self.assertEqual(receipt.callback_expires_at, 110.0)
        self.assertTrue(
            all(value.expires_at == 110.0 for value in receipt.callback_tokens)
        )

        wrong_user = self.kernel.consume_callback(token, user_id="other", chat_id="chat-1")
        self.assertEqual(wrong_user.reason, CallbackRejection.USER_MISMATCH)
        wrong_chat = self.kernel.consume_callback(token, user_id="user-1", chat_id="other")
        self.assertEqual(wrong_chat.reason, CallbackRejection.CHAT_MISMATCH)

        accepted = self.kernel.consume_callback(token, user_id="user-1", chat_id="chat-1")
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.payload.value, "yes")
        replay = self.kernel.consume_callback(token, user_id="user-1", chat_id="chat-1")
        self.assertEqual(replay.reason, CallbackRejection.REPLAY)

    def test_callbacks_expire_at_ttl_boundary(self) -> None:
        receipt = self.kernel.send_choices(
            choices_request(ttl=2.0), request_id="choices-expiring"
        )
        token = receipt.callback_tokens[0]
        self.assertEqual(receipt.callback_expires_at, 102.0)
        self.clock.advance(2.0)

        expired = self.kernel.consume_callback(token, user_id="user-1", chat_id="chat-1")
        self.assertEqual(expired.reason, CallbackRejection.EXPIRED)

    def test_sticker_requires_every_part_to_be_verified(self) -> None:
        request = StickerRequest(target(), "sticker-ref", "before", "after")
        self.host.sticker_result = [
            TransportReceipt(DeliveryStatus.VERIFIED, "text-before"),
            TransportReceipt(DeliveryStatus.UNCERTAIN),
            TransportReceipt(DeliveryStatus.VERIFIED, "text-after"),
        ]
        uncertain = self.kernel.send_sticker(request, request_id="sticker-uncertain")
        self.assertEqual(uncertain.status, DeliveryStatus.UNCERTAIN)
        self.assertEqual(uncertain.detail, "transport_continued_after_non_verified")
        self.assertEqual(
            uncertain.part_statuses,
            (DeliveryStatus.VERIFIED, DeliveryStatus.UNCERTAIN, DeliveryStatus.VERIFIED),
        )
        self.assertEqual(
            tuple(receipt.provider_delivery_id for receipt in uncertain.part_receipts),
            ("text-before", None, "text-after"),
        )
        self.assertEqual(uncertain.unexecuted_parts, 0)
        self.assertFalse(uncertain.verified_visible_completion)
        self.assertEqual(self.host.sticker_request_ids, ["sticker-uncertain"])

        self.host.sticker_result = [
            TransportReceipt(DeliveryStatus.VERIFIED, "text-before"),
            TransportReceipt(DeliveryStatus.VERIFIED, "sticker"),
            TransportReceipt(DeliveryStatus.VERIFIED, "text-after"),
        ]
        complete = self.kernel.send_sticker(request, request_id="sticker-complete")
        self.assertEqual(complete.status, DeliveryStatus.VERIFIED)
        self.assertEqual(complete.completion_mode, CompletionMode.BLOCKING)
        self.assertTrue(complete.verified_visible_completion)
        self.assertEqual(
            self.host.sticker_request_ids,
            ["sticker-uncertain", "sticker-complete"],
        )

    def test_host_exception_and_invalid_sequence_fail_closed(self) -> None:
        self.host.reaction_result = RuntimeError("synthetic")
        uncertain = self.kernel.send_reaction(
            ReactionRequest(target(), "❌"),
            request_id="reaction-uncertain-exception",
            capabilities=self.reaction_capabilities,
        )
        self.assertEqual(uncertain.status, DeliveryStatus.UNCERTAIN)
        self.assertFalse(uncertain.verified_visible_completion)

        self.host.reaction_result = object()
        unknown_reaction = self.kernel.send_reaction(
            ReactionRequest(target(), "❓"),
            request_id="reaction-unknown",
            capabilities=self.reaction_capabilities,
        )
        self.assertEqual(unknown_reaction.status, DeliveryStatus.UNKNOWN)

        self.host.reaction_result = TransportReceipt(DeliveryStatus.FAILED)
        rejected = self.kernel.send_reaction(
            ReactionRequest(target(), "👎"),
            request_id="reaction-rejected",
            capabilities=self.reaction_capabilities,
        )
        self.assertEqual(rejected.status, DeliveryStatus.FAILED)

        self.host.sticker_result = []
        unknown = self.kernel.send_sticker(
            StickerRequest(target(), "sticker-ref"), request_id="sticker-unknown"
        )
        self.assertEqual(unknown.status, DeliveryStatus.UNKNOWN)
        self.assertFalse(unknown.verified_visible_completion)

    def test_sticker_sequence_allows_only_valid_early_stop(self) -> None:
        request = StickerRequest(target(), "sticker-ref", "before", "after")

        self.host.sticker_result = [
            TransportReceipt(DeliveryStatus.VERIFIED, "before"),
            TransportReceipt(DeliveryStatus.VERIFIED, "sticker"),
        ]
        fewer = self.kernel.send_sticker(request, request_id="sticker-fewer")
        self.assertEqual(fewer.status, DeliveryStatus.UNKNOWN)
        self.assertEqual(fewer.detail, "transport_sequence_incomplete")
        self.assertFalse(fewer.verified_visible_completion)
        self.assertEqual(
            tuple(receipt.provider_delivery_id for receipt in fewer.part_receipts),
            ("before", "sticker"),
        )
        self.assertEqual(fewer.unexecuted_parts, 1)

        self.host.sticker_result = [
            TransportReceipt(DeliveryStatus.VERIFIED, "before"),
            TransportReceipt(DeliveryStatus.FAILED, detail="provider-rejected"),
        ]
        partial = self.kernel.send_sticker(request, request_id="sticker-partial")
        self.assertEqual(partial.status, DeliveryStatus.FAILED)
        self.assertEqual(partial.detail, "provider-rejected")
        self.assertEqual(partial.part_receipts[0].provider_delivery_id, "before")
        self.assertEqual(partial.part_receipts[1].detail, "provider-rejected")
        self.assertEqual(partial.unexecuted_parts, 1)

        self.host.sticker_result = [
            TransportReceipt(DeliveryStatus.VERIFIED, "before"),
            TransportReceipt(DeliveryStatus.VERIFIED, "sticker"),
            TransportReceipt(DeliveryStatus.VERIFIED, "after"),
            TransportReceipt(DeliveryStatus.VERIFIED, "extra"),
        ]
        more = self.kernel.send_sticker(request, request_id="sticker-more")
        self.assertEqual(more.status, DeliveryStatus.UNKNOWN)
        self.assertEqual(more.detail, "transport_sequence_too_long")
        self.assertEqual(len(more.part_receipts), 3)
        self.assertEqual(more.total_parts, 3)
        self.assertFalse(more.verified_visible_completion)

    def test_verified_choices_with_expired_callbacks_become_uncertain(self) -> None:
        self.host.advance_choices_before_return = 1.0
        self.host.choices_result = TransportReceipt(
            DeliveryStatus.VERIFIED,
            "choices-delivered",
        )
        receipt = self.kernel.send_choices(
            choices_request(ttl=1.0), request_id="choices-activation-failure"
        )

        self.assertEqual(receipt.status, DeliveryStatus.UNCERTAIN)
        self.assertEqual(receipt.provider_delivery_id, "choices-delivered")
        self.assertEqual(receipt.detail, "delivered_but_callback_activation_failed")
        self.assertEqual(receipt.callback_tokens, ())
        self.assertFalse(receipt.verified_visible_completion)

if __name__ == "__main__":
    unittest.main()
