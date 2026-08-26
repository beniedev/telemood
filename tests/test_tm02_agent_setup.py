from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from telemood import (
    PLAN_VERSION,
    ActionPlanError,
    InteractionCapabilities,
    RegularSticker,
    SQLiteStickerCatalog,
    TargetRef,
    action_plan_to_reply,
    check_adapter,
    parse_interaction_plan,
)


def _target() -> TargetRef:
    return TargetRef(
        channel="telegram",
        chat_id="synthetic-chat",
        message_id="synthetic-message",
        thread_id="synthetic-thread",
    )


def _plan(*actions: dict[str, object]) -> dict[str, object]:
    return {"version": PLAN_VERSION, "actions": list(actions)}


class ActionPlanContractTests(unittest.TestCase):
    def test_json_plan_is_typed_and_binding_preserves_order(self) -> None:
        data = _plan(
            {"type": "bubble", "text": "first"},
            {"type": "reaction", "target": "trigger_message", "emoji": "👍"},
            {
                "type": "choices",
                "prompt": "Pick one",
                "options": [
                    {"key": "yes", "label": "Yes"},
                    {"key": "no", "label": "No"},
                ],
            },
        )

        typed = parse_interaction_plan(json.dumps(data))
        reply = action_plan_to_reply(
            data,
            target=_target(),
            authorized_user_id="trusted-user",
        )

        self.assertEqual(typed.version, PLAN_VERSION)
        self.assertEqual(
            [type(action).__name__ for action in reply.actions],
            ["BubbleRequest", "ReactionRequest", "ChoicesRequest"],
        )
        self.assertTrue(all(action.target == _target() for action in reply.actions))
        self.assertEqual(reply.actions[-1].authorized_user_id, "trusted-user")

    def test_plan_rejects_unknown_version_type_and_model_owned_context(self) -> None:
        bad_plans = (
            {"version": "unknown", "actions": [{"type": "bubble", "text": "x"}]},
            _plan({"type": "unknown", "text": "x"}),
            _plan({"type": "bubble", "text": "x", "chat_id": "not-trusted"}),
            _plan(
                {
                    "type": "reaction",
                    "target": "provider-message-id",
                    "emoji": "👍",
                }
            ),
            _plan({"type": "sticker", "sticker": {"kind": "catalog", "id": "x"}, "file_id": "raw"}),
        )
        for data in bad_plans:
            with self.subTest(data=data), self.assertRaises(ActionPlanError):
                action_plan_to_reply(data, _target())

    def test_choices_are_strict(self) -> None:
        with self.assertRaises(ActionPlanError):
            parse_interaction_plan(_plan({"type": "bubble", "text": ""}))
        with self.assertRaises(ActionPlanError):
            parse_interaction_plan(
                _plan(
                    {
                        "type": "choices",
                        "prompt": "Duplicate",
                        "options": [
                            {"key": "same", "label": "A"},
                            {"key": "same", "label": "B"},
                        ],
                    }
                )
            )
        with self.assertRaises(ActionPlanError):
            parse_interaction_plan(
                _plan(
                    {
                        "type": "choices",
                        "prompt": "Pick",
                        "options": [
                            {"key": "yes", "label": "Yes"},
                            {"key": "no", "label": "No"},
                        ],
                        "callback_ttl_seconds": 999999,
                    }
                )
            )

        reply = action_plan_to_reply(
            _plan(
                {
                    "type": "choices",
                    "prompt": "Pick",
                    "options": [
                        {"key": "yes", "label": "Yes"},
                        {"key": "no", "label": "No"},
                    ],
                }
            ),
            _target(),
            authorized_user_id="trusted-user",
            callback_ttl_seconds=60,
        )
        self.assertEqual(reply.actions[0].callback_ttl_seconds, 60)
        with self.assertRaises(ActionPlanError):
            parse_interaction_plan(
                _plan(
                    {
                        "type": "choices",
                        "prompt": "Too few",
                        "options": [{"key": "one", "label": "One"}],
                    }
                )
            )

    def test_bubble_normalization_expands_in_place(self) -> None:
        reply = action_plan_to_reply(
            _plan(
                {"type": "bubble", "text": "一。" + ("长" * 9)},
                {"type": "reaction", "target": "trigger_message", "emoji": "👍"},
                {"type": "bubble", "text": "tail"},
            ),
            _target(),
            max_bubble_length=5,
        )

        self.assertEqual(
            [type(action).__name__ for action in reply.actions],
            ["BubbleRequest", "BubbleRequest", "BubbleRequest", "ReactionRequest", "BubbleRequest"],
        )
        self.assertEqual("".join(action.text for action in reply.actions[:3]), "一。" + ("长" * 9))

    def test_plan_sticker_resolves_only_inside_trusted_bot_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SQLiteStickerCatalog(Path(directory) / "stickers.sqlite3")
            stored = catalog.remember(
                RegularSticker(
                    bot_namespace="bot-a",
                    file_id="provider-file-a",
                    file_unique_id="provider-unique",
                )
            )
            catalog.remember(
                RegularSticker(
                    bot_namespace="bot-b",
                    file_id="provider-file-b",
                    file_unique_id="provider-unique",
                )
            )
            data = _plan(
                {
                    "type": "sticker",
                    "sticker": {"kind": "catalog", "id": stored.catalog_id},
                }
            )

            reply = action_plan_to_reply(
                data,
                _target(),
                bot_namespace="bot-a",
                sticker_catalog=catalog,
            )

            self.assertEqual(reply.actions[0].sticker_ref, "provider-file-a")
            with self.assertRaises(ActionPlanError):
                action_plan_to_reply(
                    data,
                    _target(),
                    bot_namespace="bot-b",
                    sticker_catalog=catalog,
                )


class AdapterStaticChecksTests(unittest.TestCase):
    def test_check_adapter_passes_sync_adapter_without_invoking_it(self) -> None:
        class SyncAdapter:
            def send_bubble(self, request_id, request):  # noqa: ARG002
                raise AssertionError("static check must not call transport")

            def send_reaction(self, request_id, request):  # noqa: ARG002
                raise AssertionError("static check must not call transport")

            def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
                raise AssertionError("static check must not call transport")

            def send_sticker_sequence(self, request_id, request, parts):  # noqa: ARG002
                raise AssertionError("static check must not call transport")

        result = check_adapter(SyncAdapter())
        self.assertTrue(result.ok)
        self.assertTrue(result.static_only)
        self.assertFalse(result.live_delivery_verified)
        self.assertTrue(all(method.passed for method in result.methods))

    def test_adapter_check_selects_sync_or_async_mode(self) -> None:
        class Missing:
            pass

        self.assertFalse(check_adapter(Missing()).ok)

        class AsyncAdapter:
            async def send_bubble(self, request_id, request):  # noqa: ARG002
                return None

            async def send_reaction(self, request_id, request):  # noqa: ARG002
                return None

            async def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
                return None

            async def send_sticker_sequence(self, request_id, request, parts):  # noqa: ARG002
                return None

        self.assertFalse(check_adapter(AsyncAdapter()).ok)
        self.assertTrue(check_adapter(AsyncAdapter(), mode="async").ok)
        with self.assertRaises(ValueError):
            check_adapter(AsyncAdapter(), mode="threaded")

        class BadSignatures:
            def send_bubble(self, request_id):  # noqa: ARG002
                return None

            def send_reaction(self, request_id, request, extra):  # noqa: ARG002
                return None

            def send_choices(self, request_id):  # noqa: ARG002
                return None

            def send_sticker_sequence(self):  # noqa: ARG002
                return None

        self.assertFalse(check_adapter(BadSignatures()).ok)

    def test_descriptor_properties_are_not_invoked(self) -> None:
        class PropertyAdapter:
            accesses = 0

            @property
            def send_bubble(self):
                self.accesses += 1
                raise AssertionError

            send_reaction = send_bubble
            send_choices = send_bubble
            send_sticker_sequence = send_bubble

        adapter = PropertyAdapter()
        self.assertFalse(check_adapter(adapter).ok)
        self.assertEqual(adapter.accesses, 0)


class CapabilityDefaultsTests(unittest.TestCase):
    def test_reaction_capabilities_are_conservative(self) -> None:
        capabilities = InteractionCapabilities()
        self.assertFalse(capabilities.can_send_reactions)
        self.assertFalse(capabilities.inbound_reactions_available)


if __name__ == "__main__":
    unittest.main()
