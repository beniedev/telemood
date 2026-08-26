from __future__ import annotations

import math
import unittest

from hermes_telegram_interaction import (
    CallbackPayload,
    CallbackRejection,
    CallbackResolution,
    IncomingReaction,
    IncomingSticker,
    InteractionCapabilities,
    InteractionKind,
    TargetRef,
    ActionPlanError,
    action_plan_to_reply,
    check_adapter,
    split_semantic_bubbles,
)


def _target() -> TargetRef:
    return TargetRef(channel="telegram", chat_id="chat", message_id="message", thread_id="thread")


def _choices_action() -> dict[str, object]:
    return {
        "kind": "choices",
        "prompt": "Pick one",
        "options": [
            {"key": "yes", "label": "Yes"},
            {"key": "no", "label": "No"},
        ],
        "callback_ttl_seconds": 1200,
    }


class ActionPlanContractTests(unittest.TestCase):
    def test_action_plan_accepts_mapping_and_list_and_preserves_order(self) -> None:
        plan = {
            "actions": [
                {"kind": "bubble", "text": "first"},
                {"kind": "reaction", "emoji": "👍"},
                {"kind": "sticker", "sticker_ref": "sticker-ref"},
                _choices_action(),
            ],
        }
        list_plan = [
            {"kind": "sticker", "sticker_ref": "only-sticker"},
            {"kind": "bubble", "text": "second"},
        ]
        trusted = _target()

        mapping_reply = action_plan_to_reply(plan, target=trusted, authorized_user_id="trusted-user")
        list_reply = action_plan_to_reply(list_plan, target=trusted)

        self.assertEqual(
            [type(action).__name__ for action in mapping_reply.actions],
            ["BubbleRequest", "ReactionRequest", "StickerRequest", "ChoicesRequest"],
        )
        self.assertEqual(mapping_reply.actions[0].target, trusted)
        self.assertEqual(mapping_reply.actions[0].text, "first")
        self.assertEqual(mapping_reply.actions[1].target, trusted)
        self.assertEqual(mapping_reply.actions[1].emoji, "👍")
        self.assertEqual(mapping_reply.actions[2].target, trusted)
        self.assertEqual(mapping_reply.actions[2].sticker_ref, "sticker-ref")
        self.assertEqual(mapping_reply.actions[3].target, trusted)
        self.assertEqual(
            mapping_reply.actions[3].authorized_user_id,
            "trusted-user",
        )
        self.assertEqual(
            tuple((option.key, option.label) for option in mapping_reply.actions[3].options),
            (("yes", "Yes"), ("no", "No")),
        )
        self.assertEqual([type(action).__name__ for action in list_reply.actions], ["StickerRequest", "BubbleRequest"])
        self.assertEqual(list_reply.actions[0].target, trusted)
        self.assertEqual(list_reply.actions[1].target, trusted)

    def test_plan_cannot_override_or_inject_target_fields(self) -> None:
        trusted = _target()
        base = {"kind": "bubble", "text": "hello", "target": {"channel": "evil", "chat_id": "chat"}}
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([base], target=trusted)

        with self.assertRaises(ActionPlanError):
            action_plan_to_reply(
                [
                    {
                        "kind": "choices",
                        "prompt": "Pick one",
                        "options": [{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}],
                        "authorized_user_id": "attacker",
                    }
                ],
                target=trusted,
                authorized_user_id="trusted",
            )

    def test_plan_rejects_unknown_root_action_option_keys(self) -> None:
        trusted = _target()
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply({"not_actions": [{"kind": "bubble", "text": "x"}]}, trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply(
                {"actions": [{"kind": "reaction", "emoji": "👍", "chat": "hijack"}]},
                trusted,
            )
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply(
                {"actions": [{"kind": "choices", "prompt": "Pick", "options": [{"key": "yes", "label": "Yes", "extra": "bad"}]}]},
                trusted,
                authorized_user_id="trusted",
            )

    def test_plan_rejects_unknown_kinds_and_non_mapping_structures(self) -> None:
        trusted = _target()
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply("not-a-plan", trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply({"actions": {}}, trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply({"actions": []}, trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([{"kind": "unknown", "text": "x"}], trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([{"kind": "bubble"}], trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([{"kind": "choices", "prompt": "pick", "options": "nope"}], trusted, authorized_user_id="trusted")

    def test_plan_rejects_choices_without_trusted_authorized_user(self) -> None:
        trusted = _target()
        action = _choices_action()
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([action], trusted)
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([action], trusted, authorized_user_id="   ")
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([{"kind": "choices", "prompt": "pick", "options": [{"key": "x", "label": "X"}, {"key": "y", "label": "Y"}], "callback_ttl_seconds": -30}], trusted, authorized_user_id="trusted")

    def test_plan_rejects_bad_ttls(self) -> None:
        trusted = _target()
        action = _choices_action()
        for ttl in (True, math.inf, math.nan, 0, -1):
            with self.assertRaises(ActionPlanError):
                action_plan_to_reply(
                    [{"kind": "choices", "prompt": "Pick", "options": action["options"], "callback_ttl_seconds": ttl}],
                    trusted,
                    authorized_user_id="trusted",
                )

    def test_plan_invalid_choice_shapes_should_be_actionplan_error(self) -> None:
        trusted = _target()
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply(
                [{"kind": "choices", "prompt": "single", "options": [{"key": "a", "label": "A"}]}],
                trusted,
                authorized_user_id="trusted",
            )
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply(
                [{"kind": "choices", "prompt": "dupe", "options": [{"key": "a", "label": "A"}, {"key": "a", "label": "A2"}]}],
                trusted,
                authorized_user_id="trusted",
            )


class AdapterStaticChecksTests(unittest.TestCase):
    def test_check_adapter_passes_sync_adapter_as_static_check_only(self) -> None:
        class SyncAdapter:
            def send_bubble(self, request_id, request):  # noqa: ARG002
                raise AssertionError("runtime call must not happen in static check")

            def send_reaction(self, request_id, request):  # noqa: ARG002
                raise AssertionError("runtime call must not happen in static check")

            def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
                raise AssertionError("runtime call must not happen in static check")

            def send_sticker_sequence(self, request_id, request, parts):  # noqa: ARG002
                raise AssertionError("runtime call must not happen in static check")

        adapter = SyncAdapter()
        result = check_adapter(adapter)
        self.assertTrue(result.ok)
        self.assertTrue(result.static_only)
        self.assertFalse(result.live_delivery_verified)
        self.assertEqual(result.issues, ())
        self.assertTrue(all(method.passed for method in result.methods))
        self.assertEqual(tuple(method.name for method in result.methods), ("send_bubble", "send_reaction", "send_choices", "send_sticker_sequence"))
        self.assertIsInstance(tuple(method.detail for method in result.methods), tuple)

    def test_adapter_check_distinguishes_missing_non_callable_async_and_signature(self) -> None:
        class MissingMethodAdapter:
            def send_bubble(self, request_id, request):  # noqa: ARG002
                pass

            def send_reaction(self, request_id, request):  # noqa: ARG002
                pass

            def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
                pass

        missing = check_adapter(MissingMethodAdapter())
        sticker_check = next(method for method in missing.methods if method.name == "send_sticker_sequence")
        self.assertFalse(sticker_check.present)
        self.assertFalse(sticker_check.callable)
        self.assertFalse(sticker_check.passed)
        self.assertEqual(sticker_check.detail, "missing")

        class NonCallableAdapter:
            send_bubble = 1
            send_reaction = 2
            send_choices = 3
            send_sticker_sequence = 4

        non_callable = check_adapter(NonCallableAdapter())
        self.assertTrue(all(not method.callable for method in non_callable.methods))
        self.assertTrue(all(not method.passed for method in non_callable.methods))

        class AsyncAdapter:
            async def send_bubble(self, request_id, request):  # noqa: ARG002
                return None

            async def send_reaction(self, request_id, request):  # noqa: ARG002
                return None

            async def send_choices(self, request_id, request, callback_tokens):  # noqa: ARG002
                return None

            async def send_sticker_sequence(self, request_id, request, parts):  # noqa: ARG002
                return None

        async_adapter = check_adapter(AsyncAdapter())
        self.assertTrue(all(not method.synchronous for method in async_adapter.methods))
        self.assertTrue(all(not method.passed for method in async_adapter.methods))

        class SignatureMismatchAdapter:
            def send_bubble(self, request_id):  # noqa: ARG002
                return None

            def send_reaction(self, request_id, request, extra):  # noqa: ARG002
                return None

            def send_choices(self, request_id, request, callback_tokens, extra):  # noqa: ARG002
                return None

            def send_sticker_sequence(self, request_id, request, parts, extra):  # noqa: ARG002
                return None

        mismatch = check_adapter(SignatureMismatchAdapter())
        self.assertTrue(all(not method.signature_compatible for method in mismatch.methods))
        self.assertEqual(len(mismatch.issues), 4)

    def test_descriptor_properties_are_not_invoked_in_static_check(self) -> None:
        class PropertyAdapter:
            _accessed = {"send_bubble": 0, "send_reaction": 0, "send_choices": 0, "send_sticker_sequence": 0}

            @property
            def send_bubble(self):
                self._accessed["send_bubble"] += 1
                raise AssertionError("descriptor should not be invoked")

            @property
            def send_reaction(self):
                self._accessed["send_reaction"] += 1
                raise AssertionError("descriptor should not be invoked")

            @property
            def send_choices(self):
                self._accessed["send_choices"] += 1
                raise AssertionError("descriptor should not be invoked")

            @property
            def send_sticker_sequence(self):
                self._accessed["send_sticker_sequence"] += 1
                raise AssertionError("descriptor should not be invoked")

        adapter = PropertyAdapter()
        result = check_adapter(adapter)
        self.assertFalse(result.passed)
        self.assertTrue(all(method.present for method in result.methods))
        self.assertTrue(all(not method.callable for method in result.methods))
        self.assertTrue(all(not method.detail == "missing" for method in result.methods))
        self.assertEqual(adapter._accessed, {"send_bubble": 0, "send_reaction": 0, "send_choices": 0, "send_sticker_sequence": 0})


class DocumentationAlignmentTests(unittest.TestCase):
    def test_action_plan_discriminator_is_kind(self) -> None:
        trusted = _target()
        with self.assertRaises(ActionPlanError):
            action_plan_to_reply([{"type": "bubble", "text": "legacy"}], trusted)

    def test_capability_field_names_are_current(self) -> None:
        capabilities = InteractionCapabilities(
            can_send_reactions=True,
            can_receive_reactions=True,
            available_reactions=("👍",),
        )
        self.assertTrue(capabilities.can_send_emoji("👍"))
        self.assertFalse(capabilities.can_send_emoji("❤️"))
        self.assertTrue(capabilities.can_send_reactions)
        self.assertTrue(capabilities.can_receive_reactions)
        self.assertFalse(hasattr(capabilities, "supports_reactions"))

    def test_callback_resolution_has_expected_shape(self) -> None:
        payload = CallbackPayload(InteractionKind.CHOICES, "request-id", "yes")
        accepted = CallbackResolution(True, payload=payload)
        self.assertTrue(accepted.accepted)
        self.assertIs(accepted.payload, payload)
        self.assertIsNone(accepted.reason)

        rejected = CallbackResolution(False, reason=CallbackRejection.USER_MISMATCH)
        self.assertFalse(rejected.accepted)
        self.assertIsNone(rejected.payload)
        self.assertIs(rejected.reason, CallbackRejection.USER_MISMATCH)

    def test_incoming_reaction_shape(self) -> None:
        with self.assertRaises(ValueError):
            IncomingReaction(target=None, emoji="👍", user_id="user")
        with self.assertRaises(ValueError):
            IncomingReaction(target=_target(), emoji="👍", user_id=None)

        user_reaction = IncomingReaction(target=_target(), emoji="👍", user_id="user")
        self.assertTrue(user_reaction.is_user_event)

        bot_reaction = IncomingReaction(
            target=_target(),
            emoji="👍",
            user_id="user",
            bot_generated=True,
        )
        self.assertFalse(bot_reaction.is_user_event)

    def test_incoming_sticker_requires_bot_namespace(self) -> None:
        with self.assertRaises(ValueError):
            IncomingSticker(bot_namespace="", file_id="fid", file_unique_id="uid")

        sticker = IncomingSticker(
            bot_namespace="bot-namespace",
            file_id="fid",
            file_unique_id="uid",
        )
        self.assertEqual(sticker.bot_namespace, "bot-namespace")

    def test_split_semantic_bubbles_is_explicit_splitter(self) -> None:
        parts = split_semantic_bubbles("This is a deliberately long sentence.", max_length=10)
        self.assertIsInstance(parts, tuple)
        self.assertGreaterEqual(len(parts), 1)
        self.assertTrue(all(isinstance(part, str) and part for part in parts))
        self.assertTrue(all(len(part) <= 10 for part in parts))


if __name__ == "__main__":
    unittest.main()
