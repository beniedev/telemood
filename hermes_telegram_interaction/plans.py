"""Strict conversion from ordinary action-plan data to public requests."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

from .contracts import (
    BubbleRequest,
    ChoiceOption,
    ChoicesRequest,
    ReactionRequest,
    RichReply,
    StickerRequest,
    TargetRef,
)


class ActionPlanError(ValueError):
    """The supplied ordinary mapping/list is not a valid rich action plan."""


_ROOT_KEYS = {"actions"}
_ACTION_KEYS = {
    "bubble": {"kind", "text"},
    "reaction": {"kind", "emoji"},
    "sticker": {"kind", "sticker_ref"},
    "choices": {"kind", "prompt", "options", "callback_ttl_seconds"},
}


def action_plan_to_reply(
    plan: Mapping[str, Any] | list[Mapping[str, Any]],
    target: TargetRef,
    authorized_user_id: str | None = None,
) -> RichReply:
    """Build a trusted-target ``RichReply`` from a plain mapping or list.

    The model-controlled plan contains only action content.  ``target`` and
    ``authorized_user_id`` are supplied by the trusted host and never read
    from plan data.
    """

    if not isinstance(target, TargetRef):
        raise TypeError("target must be a TargetRef supplied by the host")
    actions = _extract_actions(plan)
    built = []
    for index, raw_action in enumerate(actions):
        built.append(
            _build_action(
                raw_action,
                target=target,
                authorized_user_id=authorized_user_id,
                index=index,
            )
        )
    return RichReply(tuple(built))


def _extract_actions(plan: object) -> list[Mapping[str, Any]]:
    if isinstance(plan, Mapping):
        _check_keys(plan, _ROOT_KEYS, required=_ROOT_KEYS, context="plan")
        actions = plan["actions"]
    elif isinstance(plan, list):
        actions = plan
    else:
        raise ActionPlanError("plan must be a mapping with actions or a list")
    if not isinstance(actions, list):
        raise ActionPlanError("plan actions must be a list")
    if not actions:
        raise ActionPlanError("plan actions must not be empty")
    if not all(isinstance(action, Mapping) for action in actions):
        raise ActionPlanError("each action must be a mapping")
    return actions


def _build_action(
    raw_action: Mapping[str, Any],
    *,
    target: TargetRef,
    authorized_user_id: str | None,
    index: int,
) -> object:
    if "kind" not in raw_action:
        raise ActionPlanError(f"action {index} is missing kind")
    kind = raw_action["kind"]
    if not isinstance(kind, str) or kind not in _ACTION_KEYS:
        raise ActionPlanError(f"action {index} has an unknown kind")
    _check_keys(
        raw_action,
        _ACTION_KEYS[kind],
        required=_ACTION_KEYS[kind] - {"callback_ttl_seconds"},
        context=f"action {index}",
    )

    if kind == "bubble":
        text = _text(raw_action["text"], "bubble text")
        return _from_plan_value(
            lambda: BubbleRequest(target=target, text=text),
            index=index,
        )
    if kind == "reaction":
        if target.message_id is None:
            raise ValueError("trusted target must include message_id for reaction")
        emoji = _text(raw_action["emoji"], "reaction emoji")
        return _from_plan_value(
            lambda: ReactionRequest(target=target, emoji=emoji),
            index=index,
        )
    if kind == "sticker":
        sticker_ref = _text(raw_action["sticker_ref"], "sticker_ref")
        return _from_plan_value(
            lambda: StickerRequest(target=target, sticker_ref=sticker_ref),
            index=index,
        )

    if authorized_user_id is None:
        raise ActionPlanError("choices require trusted authorized_user_id")
    if not isinstance(authorized_user_id, str) or not authorized_user_id.strip():
        raise ActionPlanError("authorized_user_id must be a trusted non-empty string")
    options = raw_action["options"]
    if not isinstance(options, list) or not options:
        raise ActionPlanError("choices options must be a non-empty list")
    built_options = []
    for option_index, raw_option in enumerate(options):
        if not isinstance(raw_option, Mapping):
            raise ActionPlanError(f"choice option {option_index} must be a mapping")
        _check_keys(
            raw_option,
            {"key", "label"},
            required={"key", "label"},
            context=f"choice option {option_index}",
        )
        key = _text(raw_option["key"], "choice key")
        label = _text(raw_option["label"], "choice label")
        built_options.append(
            _from_plan_value(
                lambda: ChoiceOption(key=key, label=label),
                index=index,
            )
        )
    ttl = raw_action.get("callback_ttl_seconds", 1800.0)
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        raise ActionPlanError("callback_ttl_seconds must be a finite number")
    if not isfinite(float(ttl)) or float(ttl) <= 0:
        raise ActionPlanError("callback_ttl_seconds must be positive and finite")
    prompt = _text(raw_action["prompt"], "choice prompt")
    return _from_plan_value(
        lambda: ChoicesRequest(
            target=target,
            prompt=prompt,
            options=tuple(built_options),
            authorized_user_id=authorized_user_id,
            callback_ttl_seconds=float(ttl),
        ),
        index=index,
    )


def _from_plan_value(factory, *, index: int):
    try:
        return factory()
    except (TypeError, ValueError) as exc:
        raise ActionPlanError(f"action {index} contains invalid values: {exc}") from exc


def _check_keys(
    value: Mapping[object, object],
    allowed: set[str],
    *,
    required: set[str],
    context: str,
) -> None:
    keys = set(value.keys())
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise ActionPlanError(f"{context} contains unknown keys: {sorted(map(str, unknown))}")
    if missing:
        raise ActionPlanError(f"{context} is missing required keys: {sorted(missing)}")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ActionPlanError(f"{field_name} must be a string")
    return value
