from __future__ import annotations

import inspect
import unittest
from itertools import count

from hermes_telegram_interaction import (
    CallbackPayload,
    CallbackRegistry,
    CallbackStore,
    CallbackToken,
    ChoiceOption,
    ChoicesRequest,
    DeliveryStatus,
    InteractionKernel,
    InteractionKind,
    TargetRef,
    TransportReceipt,
)


class _IncompleteStore:
    def register(self, **_kwargs):
        return None

    def consume(self, *_args, **_kwargs):
        return None


class _BulkStore:
    def __init__(self) -> None:
        tokens = count()
        self.registry = CallbackRegistry(
            token_factory=lambda: f"hti2:synthetic-{next(tokens)}"
        )

    def register(self, **kwargs):
        return self.registry.register(**kwargs)

    def activate_all(self, tokens):
        return all(self.registry.activate(token) for token in tokens)

    def revoke_all(self, tokens):
        return all(self.registry.revoke(token) for token in tokens)

    def consume(self, *args, **kwargs):
        return self.registry.consume(*args, **kwargs)


class _ChoicesHost:
    def send_choices(self, _request_id, _request, callback_tokens):
        self.callback_tokens = dict(callback_tokens)
        return TransportReceipt(DeliveryStatus.VERIFIED)


class PublicContractTests(unittest.TestCase):
    def test_callback_registry_implements_callback_store(self) -> None:
        registry = CallbackRegistry(token_factory=lambda: "hti2:synthetic")
        self.assertIsInstance(registry, CallbackStore)

    def test_bulk_store_shape_is_accepted(self) -> None:
        self.assertIsInstance(_BulkStore(), CallbackStore)

    def test_incomplete_two_method_store_is_rejected(self) -> None:
        self.assertNotIsInstance(_IncompleteStore(), CallbackStore)

    def test_kernel_rejects_incomplete_callback_store(self) -> None:
        with self.assertRaises(TypeError):
            InteractionKernel(_ChoicesHost(), callbacks=_IncompleteStore())

    def test_kernel_adapts_atomic_bulk_activation(self) -> None:
        store = _BulkStore()
        kernel = InteractionKernel(_ChoicesHost(), callbacks=store)
        request = ChoicesRequest(
            TargetRef(channel="synthetic", chat_id="chat"),
            "Choose",
            (ChoiceOption("yes", "Yes"), ChoiceOption("no", "No")),
            "user",
        )
        receipt = kernel.send_choices(request, request_id="request")
        self.assertEqual(receipt.status, DeliveryStatus.VERIFIED)
        self.assertEqual(len(receipt.callback_tokens), 2)
        resolved = store.consume(
            receipt.callback_tokens[0],
            user_id="user",
            chat_id="chat",
        )
        self.assertTrue(resolved.accepted)

    def test_callback_store_signatures_are_stable(self) -> None:
        register = inspect.signature(CallbackStore.register)
        activate = inspect.signature(CallbackStore.activate)
        revoke = inspect.signature(CallbackStore.revoke)
        consume = inspect.signature(CallbackStore.consume)
        self.assertEqual(
            tuple(register.parameters),
            ("self", "user_id", "chat_id", "payload", "ttl_seconds", "thread_id"),
        )
        self.assertEqual(tuple(activate.parameters), ("self", "token"))
        self.assertEqual(tuple(revoke.parameters), ("self", "token"))
        self.assertEqual(
            tuple(consume.parameters),
            ("self", "token", "user_id", "chat_id", "thread_id"),
        )

    def test_registry_round_trip_uses_public_types(self) -> None:
        registry = CallbackRegistry(token_factory=lambda: "hti2:synthetic")
        token = registry.register(
            user_id="user",
            chat_id="chat",
            payload=CallbackPayload(InteractionKind.CHOICES, "request", "yes"),
            ttl_seconds=10,
        )
        self.assertIsInstance(token, CallbackToken)
        self.assertTrue(registry.activate(token))
        result = registry.consume(token, user_id="user", chat_id="chat")
        self.assertTrue(result.accepted)


if __name__ == "__main__":
    unittest.main()
