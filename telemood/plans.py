"""Versioned, strict model plans bound to trusted host context."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
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
from .stickers import StickerCatalog
from .text import split_semantic_bubbles

PLAN_VERSION = "telemood.plan.v1"


class ActionPlanError(ValueError):
    """The supplied model data cannot be safely bound to an interaction."""


@dataclass(frozen=True)
class BubblePlanAction:
    text: str

    def __post_init__(self) -> None:
        _plan_text(self.text, "bubble text", allow_layout=True)


@dataclass(frozen=True)
class ReactionPlanAction:
    emoji: str
    target: str = "trigger_message"

    def __post_init__(self) -> None:
        if self.target != "trigger_message":
            raise ActionPlanError("reaction target must be trigger_message")
        _plan_text(self.emoji, "reaction emoji")
        if len(self.emoji) > 32:
            raise ActionPlanError("reaction emoji is too long")


@dataclass(frozen=True)
class StickerPlanAction:
    catalog_id: str

    def __post_init__(self) -> None:
        _plan_text(self.catalog_id, "sticker catalog id")
        if "://" in self.catalog_id:
            raise ActionPlanError("sticker catalog id must be a logical reference")


@dataclass(frozen=True)
class ChoicesPlanAction:
    prompt: str
    options: tuple[ChoiceOption, ...]

    def __post_init__(self) -> None:
        _plan_text(self.prompt, "choice prompt")
        options = tuple(self.options)
        if not 2 <= len(options) <= 4:
            raise ActionPlanError("choices must contain between two and four options")
        if not all(isinstance(option, ChoiceOption) for option in options):
            raise ActionPlanError("choices options must contain ChoiceOption values")
        if len({option.key for option in options}) != len(options):
            raise ActionPlanError("choice keys must be unique")
        object.__setattr__(self, "options", options)


PlanAction = BubblePlanAction | ReactionPlanAction | StickerPlanAction | ChoicesPlanAction


@dataclass(frozen=True)
class InteractionPlan:
    version: str
    actions: tuple[PlanAction, ...]

    def __post_init__(self) -> None:
        if self.version != PLAN_VERSION:
            raise ActionPlanError(f"plan version must be {PLAN_VERSION}")
        actions = tuple(self.actions)
        if not actions:
            raise ActionPlanError("plan actions must not be empty")
        if not all(
            isinstance(
                action,
                (BubblePlanAction, ReactionPlanAction, StickerPlanAction, ChoicesPlanAction),
            )
            for action in actions
        ):
            raise ActionPlanError("plan contains an unsupported action")
        object.__setattr__(self, "actions", actions)


@dataclass(frozen=True)
class PlanContext:
    """Trusted values that model output is never allowed to provide."""

    target: TargetRef
    authorized_user_id: str | None = None
    bot_namespace: str | None = None
    callback_ttl_seconds: float = 1800.0

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetRef):
            raise TypeError("target must be a TargetRef supplied by the host")
        if self.authorized_user_id is not None:
            _plan_text(self.authorized_user_id, "authorized_user_id")
        if self.bot_namespace is not None:
            _plan_text(self.bot_namespace, "bot_namespace")
        if (
            isinstance(self.callback_ttl_seconds, bool)
            or not isinstance(self.callback_ttl_seconds, (int, float))
            or not isfinite(float(self.callback_ttl_seconds))
            or float(self.callback_ttl_seconds) <= 0
        ):
            raise ValueError("callback_ttl_seconds must be positive and finite")
        object.__setattr__(
            self,
            "callback_ttl_seconds",
            float(self.callback_ttl_seconds),
        )


def parse_interaction_plan(data: str | Mapping[str, Any]) -> InteractionPlan:
    """Parse strict JSON-compatible model output without trusted identifiers."""

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ActionPlanError("plan must be valid JSON") from exc
    if not isinstance(data, Mapping):
        raise ActionPlanError("plan must be a mapping")
    _check_keys(data, {"version", "actions"}, required={"version", "actions"}, context="plan")
    if data["version"] != PLAN_VERSION:
        raise ActionPlanError(f"plan version must be {PLAN_VERSION}")
    raw_actions = data["actions"]
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ActionPlanError("plan actions must be a non-empty list")
    return InteractionPlan(
        version=PLAN_VERSION,
        actions=tuple(_parse_action(value, index) for index, value in enumerate(raw_actions)),
    )


def bind_interaction_plan(
    plan: InteractionPlan,
    context: PlanContext,
    *,
    sticker_catalog: StickerCatalog | None = None,
    max_bubble_length: int = 4096,
) -> RichReply:
    """Bind a parsed plan to host-owned targets, users, and sticker file ids."""

    if not isinstance(plan, InteractionPlan):
        raise TypeError("plan must be InteractionPlan")
    if not isinstance(context, PlanContext):
        raise TypeError("context must be PlanContext")
    built = []
    for index, action in enumerate(plan.actions):
        try:
            if isinstance(action, BubblePlanAction):
                built.extend(
                    BubbleRequest(context.target, part)
                    for part in split_semantic_bubbles(
                        action.text,
                        max_length=max_bubble_length,
                    )
                )
            elif isinstance(action, ReactionPlanAction):
                built.append(ReactionRequest(context.target, action.emoji))
            elif isinstance(action, StickerPlanAction):
                if sticker_catalog is None or context.bot_namespace is None:
                    raise ActionPlanError(
                        "sticker action requires trusted bot_namespace and sticker catalog"
                    )
                sticker = sticker_catalog.resolve(context.bot_namespace, action.catalog_id)
                if sticker is None:
                    raise ActionPlanError("sticker catalog id is unknown in this bot namespace")
                built.append(StickerRequest(context.target, sticker.file_id))
            else:
                if context.authorized_user_id is None:
                    raise ActionPlanError("choices require trusted authorized_user_id")
                built.append(
                    ChoicesRequest(
                        target=context.target,
                        prompt=action.prompt,
                        options=action.options,
                        authorized_user_id=context.authorized_user_id,
                        callback_ttl_seconds=context.callback_ttl_seconds,
                    )
                )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ActionPlanError):
                raise
            raise ActionPlanError(f"action {index} cannot be bound: {exc}") from exc
    return RichReply(tuple(built))


def action_plan_to_reply(
    plan: str | Mapping[str, Any],
    target: TargetRef,
    authorized_user_id: str | None = None,
    *,
    bot_namespace: str | None = None,
    sticker_catalog: StickerCatalog | None = None,
    max_bubble_length: int = 4096,
    callback_ttl_seconds: float = 1800.0,
) -> RichReply:
    """Convenience wrapper for parsing and trusted-context binding."""

    return bind_interaction_plan(
        parse_interaction_plan(plan),
        PlanContext(
            target=target,
            authorized_user_id=authorized_user_id,
            bot_namespace=bot_namespace,
            callback_ttl_seconds=callback_ttl_seconds,
        ),
        sticker_catalog=sticker_catalog,
        max_bubble_length=max_bubble_length,
    )


def _parse_action(raw_action: object, index: int) -> PlanAction:
    if not isinstance(raw_action, Mapping):
        raise ActionPlanError(f"action {index} must be a mapping")
    action_type = raw_action.get("type")
    if action_type == "bubble":
        _check_keys(
            raw_action,
            {"type", "text"},
            required={"type", "text"},
            context=f"action {index}",
        )
        return BubblePlanAction(_string(raw_action["text"], "bubble text"))
    if action_type == "reaction":
        _check_keys(
            raw_action,
            {"type", "target", "emoji"},
            required={"type", "target", "emoji"},
            context=f"action {index}",
        )
        return ReactionPlanAction(
            emoji=_string(raw_action["emoji"], "reaction emoji"),
            target=_string(raw_action["target"], "reaction target"),
        )
    if action_type == "sticker":
        _check_keys(
            raw_action,
            {"type", "sticker"},
            required={"type", "sticker"},
            context=f"action {index}",
        )
        sticker = raw_action["sticker"]
        if not isinstance(sticker, Mapping):
            raise ActionPlanError("sticker selector must be a mapping")
        _check_keys(
            sticker,
            {"kind", "id"},
            required={"kind", "id"},
            context="sticker selector",
        )
        if sticker["kind"] != "catalog":
            raise ActionPlanError("sticker selector kind must be catalog")
        return StickerPlanAction(_string(sticker["id"], "sticker catalog id"))
    if action_type == "choices":
        _check_keys(
            raw_action,
            {"type", "prompt", "options"},
            required={"type", "prompt", "options"},
            context=f"action {index}",
        )
        raw_options = raw_action["options"]
        if not isinstance(raw_options, list):
            raise ActionPlanError("choices options must be a list")
        options = []
        for option_index, raw_option in enumerate(raw_options):
            if not isinstance(raw_option, Mapping):
                raise ActionPlanError(f"choice option {option_index} must be a mapping")
            _check_keys(
                raw_option,
                {"key", "label"},
                required={"key", "label"},
                context=f"choice option {option_index}",
            )
            try:
                options.append(
                    ChoiceOption(
                        key=_string(raw_option["key"], "choice key"),
                        label=_string(raw_option["label"], "choice label"),
                    )
                )
            except ValueError as exc:
                raise ActionPlanError(f"invalid choice option {option_index}: {exc}") from exc
        return ChoicesPlanAction(
            prompt=_string(raw_action["prompt"], "choice prompt"),
            options=tuple(options),
        )
    raise ActionPlanError(f"action {index} has an unknown type")


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


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ActionPlanError(f"{field_name} must be a string")
    return value


def _plan_text(value: object, field_name: str, *, allow_layout: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionPlanError(f"{field_name} must be a non-empty string")
    allowed = "\n\r\t" if allow_layout else ""
    if any(ord(character) < 32 for character in value if character not in allowed):
        raise ActionPlanError(f"{field_name} must not contain control characters")
    return value
