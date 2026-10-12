from __future__ import annotations

import unittest

from telemood import (
    CoalescePolicy,
    InboundCoalescer,
    InboundPart,
    RestartGuard,
    can_restart,
    render_inbound,
)


class CoalescePolicyTests(unittest.TestCase):
    def test_defaults(self) -> None:
        policy = CoalescePolicy()
        self.assertEqual(policy.quiet_seconds, 2.5)
        self.assertEqual(policy.sticker_quiet_seconds, 4.5)
        self.assertEqual(policy.max_wait_seconds, 8.0)

    def test_invalid_values_are_rejected(self) -> None:
        for kwargs in (
            {"quiet_seconds": 0.0},
            {"quiet_seconds": -1.0},
            {"quiet_seconds": float("inf")},
            {"quiet_seconds": float("nan")},
            {"quiet_seconds": True},
            {"sticker_quiet_seconds": 0.0},
            {"sticker_quiet_seconds": float("nan")},
            {"max_wait_seconds": -8.0},
            {"max_wait_seconds": float("inf")},
            {"quiet_seconds": "2.5"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CoalescePolicy(**kwargs)

    def test_max_wait_must_cover_largest_quiet_window(self) -> None:
        with self.assertRaises(ValueError):
            CoalescePolicy(max_wait_seconds=4.0)
        with self.assertRaises(ValueError):
            CoalescePolicy(
                quiet_seconds=3.0,
                sticker_quiet_seconds=3.0,
                max_wait_seconds=2.0,
            )
        policy = CoalescePolicy(
            quiet_seconds=4.0,
            sticker_quiet_seconds=4.0,
            max_wait_seconds=4.0,
        )
        self.assertEqual(policy.max_wait_seconds, 4.0)


class InboundCoalescerTests(unittest.TestCase):
    def test_single_text_deadline_and_due(self) -> None:
        coalescer = InboundCoalescer()
        deadline = coalescer.add(InboundPart("text", "hello", 100.0))
        self.assertEqual(deadline, 102.5)
        self.assertEqual(coalescer.deadline(), 102.5)
        self.assertFalse(coalescer.due(102.49))
        self.assertTrue(coalescer.due(102.5))

    def test_quiet_window_restarts_on_each_part(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "first half", 0.0))
        deadline = coalescer.add(InboundPart("text", "second half", 2.0))
        self.assertEqual(deadline, 4.5)
        self.assertFalse(coalescer.due(4.49))
        self.assertTrue(coalescer.due(4.5))

    def test_sticker_alone_uses_longer_quiet_window(self) -> None:
        coalescer = InboundCoalescer()
        deadline = coalescer.add(InboundPart("sticker", "a wave", 10.0))
        self.assertEqual(deadline, 14.5)

    def test_sticker_then_text_uses_text_quiet_window(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("sticker", "a wave", 0.0))
        deadline = coalescer.add(InboundPart("text", "hi back", 3.0))
        self.assertEqual(deadline, 5.5)

    def test_text_then_sticker_uses_sticker_quiet_window(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "look at this", 0.0))
        deadline = coalescer.add(InboundPart("sticker", "a grin", 1.0))
        self.assertEqual(deadline, 5.5)

    def test_max_wait_caps_continuous_parts(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "part-0", 0.0))
        deadline = 0.0
        for index in range(1, 6):
            deadline = coalescer.add(
                InboundPart("text", f"part-{index}", 2.0 * index)
            )
        self.assertEqual(len(coalescer), 6)
        self.assertEqual(deadline, 8.0)
        self.assertFalse(coalescer.due(7.99))
        self.assertTrue(coalescer.due(8.0))

    def test_drain_returns_arrival_order_and_clears(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "one", 0.0))
        coalescer.add(InboundPart("sticker", "two", 1.0))
        coalescer.add(InboundPart("text", "three", 2.0))
        self.assertEqual(len(coalescer), 3)
        parts = coalescer.drain()
        self.assertEqual(tuple(part.text for part in parts), ("one", "two", "three"))
        self.assertEqual(len(coalescer), 0)
        self.assertIsNone(coalescer.deadline())
        self.assertFalse(coalescer.due(1_000_000.0))
        deadline = coalescer.add(InboundPart("text", "four", 3.0))
        self.assertEqual(deadline, 5.5)

    def test_out_of_order_received_at_is_rejected(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "earlier", 5.0))
        with self.assertRaises(ValueError):
            coalescer.add(InboundPart("text", "late", 4.99))
        self.assertEqual(len(coalescer), 1)
        self.assertEqual(coalescer.deadline(), 7.5)

    def test_equal_received_at_is_allowed(self) -> None:
        coalescer = InboundCoalescer()
        coalescer.add(InboundPart("text", "a", 5.0))
        deadline = coalescer.add(InboundPart("text", "b", 5.0))
        self.assertEqual(deadline, 7.5)

    def test_custom_policy_is_used(self) -> None:
        policy = CoalescePolicy(
            quiet_seconds=1.0,
            sticker_quiet_seconds=2.0,
            max_wait_seconds=5.0,
        )
        coalescer = InboundCoalescer(policy)
        deadline = coalescer.add(InboundPart("sticker", "a nod", 0.0))
        self.assertEqual(deadline, 2.0)

    def test_empty_coalescer_has_no_deadline(self) -> None:
        coalescer = InboundCoalescer()
        self.assertIsNone(coalescer.deadline())
        self.assertFalse(coalescer.due(0.0))
        self.assertEqual(len(coalescer), 0)
        self.assertEqual(coalescer.drain(), ())


class RenderInboundTests(unittest.TestCase):
    def test_order_preserved_and_text_stripped(self) -> None:
        parts = (
            InboundPart("text", "  first  ", 0.0),
            InboundPart("sticker", " a wave ", 1.0),
            InboundPart("text", "third", 2.0),
        )
        self.assertEqual(render_inbound(parts), "first\na wave\nthird")

    def test_empty_parts_are_skipped(self) -> None:
        parts = (
            InboundPart("text", "   ", 0.0),
            InboundPart("text", "kept", 1.0),
            InboundPart("sticker", "", 2.0),
        )
        self.assertEqual(render_inbound(parts), "kept")

    def test_empty_input_renders_empty(self) -> None:
        self.assertEqual(render_inbound(()), "")


class RestartGuardTests(unittest.TestCase):
    def test_fresh_guard_can_restart(self) -> None:
        self.assertTrue(RestartGuard().can_restart)

    def test_read_only_tool_preserves_restart(self) -> None:
        guard = RestartGuard(read_only_tools=("search", "read_page"))
        guard.observe_tool("search")
        self.assertTrue(guard.can_restart)

    def test_unknown_tool_blocks_restart(self) -> None:
        guard = RestartGuard(read_only_tools=("search",))
        guard.observe_tool("send_message")
        self.assertFalse(guard.can_restart)

    def test_visible_output_blocks_restart(self) -> None:
        guard = RestartGuard()
        guard.mark_visible_output()
        self.assertFalse(guard.can_restart)

    def test_can_restart_truth_table(self) -> None:
        self.assertTrue(
            can_restart(visible_output_sent=False, side_effect_tool_started=False)
        )
        self.assertFalse(
            can_restart(visible_output_sent=True, side_effect_tool_started=False)
        )
        self.assertFalse(
            can_restart(visible_output_sent=False, side_effect_tool_started=True)
        )
        self.assertFalse(
            can_restart(visible_output_sent=True, side_effect_tool_started=True)
        )


if __name__ == "__main__":
    unittest.main()
