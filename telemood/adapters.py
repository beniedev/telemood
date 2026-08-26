"""Dependency-free adapters for an already-owned Telegram client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import (
    BubbleRequest,
    CallbackToken,
    ChoicesRequest,
    DeliveryStatus,
    IncomingReactionChange,
    IncomingReactionCount,
    IncomingSticker,
    IncomingStickerEvent,
    ReactionActor,
    ReactionCount,
    ReactionRequest,
    ReactionType,
    ReactionValue,
    StickerFormat,
    StickerPart,
    StickerPartKind,
    StickerRequest,
    StickerType,
    TargetRef,
    TransportReceipt,
)


@dataclass(frozen=True)
class InjectedResult:
    """Explicit outcome returned by a small host-owned client facade."""

    accepted: bool
    provider_delivery_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be bool")
        for name in ("provider_delivery_id", "detail"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string or None")


class InjectedTelegramClient(Protocol):
    """Minimal sync facade implemented around the host's existing client."""

    def send_message(self, **kwargs: object) -> object: ...

    def set_reaction(self, **kwargs: object) -> object: ...

    def send_sticker(self, **kwargs: object) -> object: ...

    def send_choices(self, **kwargs: object) -> object: ...


class AsyncInjectedTelegramClient(Protocol):
    """Minimal async facade implemented around the host's existing client."""

    async def send_message(self, **kwargs: object) -> object: ...

    async def set_reaction(self, **kwargs: object) -> object: ...

    async def send_sticker(self, **kwargs: object) -> object: ...

    async def send_choices(self, **kwargs: object) -> object: ...


class InjectedTelegramAdapter:
    """Map Telemood requests onto a sync client the host already owns."""

    def __init__(self, client: InjectedTelegramClient) -> None:
        self.client = client

    def send_bubble(self, request_id: str, request: BubbleRequest) -> TransportReceipt:
        return _call(
            self.client.send_message,
            request_id=request_id,
            chat_id=request.target.chat_id,
            thread_id=request.target.thread_id,
            text=request.text,
        )

    def send_reaction(
        self,
        request_id: str,
        request: ReactionRequest,
    ) -> TransportReceipt:
        return _call(
            self.client.set_reaction,
            request_id=request_id,
            chat_id=request.target.chat_id,
            message_id=request.target.message_id,
            emoji=request.emoji,
        )

    def send_choices(
        self,
        request_id: str,
        request: ChoicesRequest,
        callback_tokens: Mapping[str, CallbackToken],
    ) -> TransportReceipt:
        return _call(
            self.client.send_choices,
            request_id=request_id,
            chat_id=request.target.chat_id,
            thread_id=request.target.thread_id,
            prompt=request.prompt,
            options=tuple(
                (option.label, callback_tokens[option.key].value)
                for option in request.options
            ),
        )

    def send_sticker_sequence(
        self,
        request_id: str,
        request: StickerRequest,
        parts: Sequence[StickerPart],
    ) -> tuple[TransportReceipt, ...]:
        receipts = []
        for index, part in enumerate(parts):
            if part.kind is StickerPartKind.TEXT:
                receipt = _call(
                    self.client.send_message,
                    request_id=f"{request_id}:{index}",
                    chat_id=request.target.chat_id,
                    thread_id=request.target.thread_id,
                    text=part.value,
                )
            else:
                receipt = _call(
                    self.client.send_sticker,
                    request_id=f"{request_id}:{index}",
                    chat_id=request.target.chat_id,
                    thread_id=request.target.thread_id,
                    sticker_ref=part.value,
                )
            receipts.append(receipt)
            if receipt.status is not DeliveryStatus.VERIFIED:
                break
        return tuple(receipts)


class AsyncInjectedTelegramAdapter:
    """Map Telemood requests onto an async client without bridging loops."""

    def __init__(self, client: AsyncInjectedTelegramClient) -> None:
        self.client = client

    async def send_bubble(
        self,
        request_id: str,
        request: BubbleRequest,
    ) -> TransportReceipt:
        return await _async_call(
            self.client.send_message,
            request_id=request_id,
            chat_id=request.target.chat_id,
            thread_id=request.target.thread_id,
            text=request.text,
        )

    async def send_reaction(
        self,
        request_id: str,
        request: ReactionRequest,
    ) -> TransportReceipt:
        return await _async_call(
            self.client.set_reaction,
            request_id=request_id,
            chat_id=request.target.chat_id,
            message_id=request.target.message_id,
            emoji=request.emoji,
        )

    async def send_choices(
        self,
        request_id: str,
        request: ChoicesRequest,
        callback_tokens: Mapping[str, CallbackToken],
    ) -> TransportReceipt:
        return await _async_call(
            self.client.send_choices,
            request_id=request_id,
            chat_id=request.target.chat_id,
            thread_id=request.target.thread_id,
            prompt=request.prompt,
            options=tuple(
                (option.label, callback_tokens[option.key].value)
                for option in request.options
            ),
        )

    async def send_sticker_sequence(
        self,
        request_id: str,
        request: StickerRequest,
        parts: Sequence[StickerPart],
    ) -> tuple[TransportReceipt, ...]:
        receipts = []
        for index, part in enumerate(parts):
            if part.kind is StickerPartKind.TEXT:
                receipt = await _async_call(
                    self.client.send_message,
                    request_id=f"{request_id}:{index}",
                    chat_id=request.target.chat_id,
                    thread_id=request.target.thread_id,
                    text=part.value,
                )
            else:
                receipt = await _async_call(
                    self.client.send_sticker,
                    request_id=f"{request_id}:{index}",
                    chat_id=request.target.chat_id,
                    thread_id=request.target.thread_id,
                    sticker_ref=part.value,
                )
            receipts.append(receipt)
            if receipt.status is not DeliveryStatus.VERIFIED:
                break
        return tuple(receipts)


@dataclass(frozen=True)
class NormalizedCallbackQuery:
    token: CallbackToken
    user_id: str
    target: TargetRef


def normalize_incoming_sticker(
    update: Mapping[str, Any],
    *,
    bot_namespace: str,
    thumbnail_ref: str | None = None,
    media_ref: str | None = None,
) -> IncomingStickerEvent:
    """Normalize a Bot API sticker message mapping without SDK objects."""

    update = _mapping(update, "update")
    message = _message(update)
    sticker = _mapping(message.get("sticker"), "message.sticker")
    sender_chat = message.get("sender_chat")
    sender_user = message.get("from")
    sender_kwargs: dict[str, str]
    if isinstance(sender_chat, Mapping):
        sender_kwargs = {"sender_chat_id": _identifier(sender_chat.get("id"), "sender_chat.id")}
    elif isinstance(sender_user, Mapping):
        sender_kwargs = {"sender_user_id": _identifier(sender_user.get("id"), "from.id")}
    else:
        raise ValueError("sticker message requires from or sender_chat")
    is_animated = _optional_boolean(sticker.get("is_animated"), "sticker.is_animated")
    is_video = _optional_boolean(sticker.get("is_video"), "sticker.is_video")
    if is_animated and is_video:
        raise ValueError("sticker cannot be both animated and video")
    sticker_format = (
        StickerFormat.VIDEO
        if is_video
        else StickerFormat.ANIMATED
        if is_animated
        else StickerFormat.STATIC
    )
    return IncomingStickerEvent(
        target=_target(message),
        received_at=_integer(message.get("date"), "message.date"),
        sticker=IncomingSticker(
            bot_namespace=bot_namespace,
            file_id=_text(sticker.get("file_id"), "sticker.file_id"),
            file_unique_id=_text(
                sticker.get("file_unique_id"),
                "sticker.file_unique_id",
            ),
            emoji=_optional_text(sticker.get("emoji"), "sticker.emoji"),
            set_name=_optional_text(sticker.get("set_name"), "sticker.set_name"),
            format=sticker_format,
            thumbnail_ref=thumbnail_ref,
            media_ref=media_ref,
            type=StickerType(sticker.get("type", "regular")),
        ),
        **sender_kwargs,
    )


def normalize_incoming_reaction_change(
    update: Mapping[str, Any],
) -> IncomingReactionChange:
    update = _mapping(update, "update")
    raw = _mapping(update.get("message_reaction"), "message_reaction")
    actor_chat = raw.get("actor_chat")
    user = raw.get("user")
    if isinstance(actor_chat, Mapping):
        actor = ReactionActor(chat_id=_identifier(actor_chat.get("id"), "actor_chat.id"))
    elif isinstance(user, Mapping):
        actor = ReactionActor(user_id=_identifier(user.get("id"), "user.id"))
    else:
        raise ValueError("message_reaction requires user or actor_chat")
    return IncomingReactionChange(
        target=_reaction_target(raw),
        actor=actor,
        old_reactions=_reaction_values(raw.get("old_reaction"), "old_reaction"),
        new_reactions=_reaction_values(raw.get("new_reaction"), "new_reaction"),
        changed_at=_integer(raw.get("date"), "message_reaction.date"),
    )


def normalize_incoming_reaction_count(
    update: Mapping[str, Any],
) -> IncomingReactionCount:
    update = _mapping(update, "update")
    raw = _mapping(update.get("message_reaction_count"), "message_reaction_count")
    reactions = raw.get("reactions")
    if not isinstance(reactions, list):
        raise ValueError("message_reaction_count.reactions must be a list")
    counts = []
    for index, value in enumerate(reactions):
        count = _mapping(value, f"reactions[{index}]")
        counts.append(
            ReactionCount(
                reaction=_reaction_value(
                    _mapping(count.get("type"), f"reactions[{index}].type")
                ),
                total_count=_integer(
                    count.get("total_count"),
                    f"reactions[{index}].total_count",
                ),
            )
        )
    return IncomingReactionCount(
        target=_reaction_target(raw),
        counts=tuple(counts),
        changed_at=_integer(raw.get("date"), "message_reaction_count.date"),
    )


def normalize_callback_query(update: Mapping[str, Any]) -> NormalizedCallbackQuery:
    update = _mapping(update, "update")
    raw = _mapping(update.get("callback_query"), "callback_query")
    message = _mapping(raw.get("message"), "callback_query.message")
    user = _mapping(raw.get("from"), "callback_query.from")
    return NormalizedCallbackQuery(
        token=CallbackToken(_text(raw.get("data"), "callback_query.data")),
        user_id=_identifier(user.get("id"), "callback_query.from.id"),
        target=_target(message),
    )


def _call(method, **kwargs: object) -> TransportReceipt:
    try:
        return _map_result(method(**kwargs))
    except TimeoutError:
        return TransportReceipt(DeliveryStatus.UNCERTAIN, detail="transport_timeout")
    except Exception:
        return TransportReceipt(DeliveryStatus.UNCERTAIN, detail="transport_exception")


async def _async_call(method, **kwargs: object) -> TransportReceipt:
    try:
        return _map_result(await method(**kwargs))
    except TimeoutError:
        return TransportReceipt(DeliveryStatus.UNCERTAIN, detail="transport_timeout")
    except Exception:
        return TransportReceipt(DeliveryStatus.UNCERTAIN, detail="transport_exception")


def _map_result(result: object) -> TransportReceipt:
    if isinstance(result, TransportReceipt):
        return result
    if isinstance(result, InjectedResult):
        return TransportReceipt(
            DeliveryStatus.VERIFIED if result.accepted else DeliveryStatus.FAILED,
            provider_delivery_id=result.provider_delivery_id,
            detail=result.detail,
        )
    return TransportReceipt(DeliveryStatus.UNKNOWN, detail="invalid_client_result")


def _message(update: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        value = update.get(key)
        if isinstance(value, Mapping):
            return value
    raise ValueError("update does not contain a message")


def _target(message: Mapping[str, Any]) -> TargetRef:
    chat = _mapping(message.get("chat"), "message.chat")
    thread = message.get("message_thread_id")
    return TargetRef(
        channel="telegram",
        chat_id=_identifier(chat.get("id"), "chat.id"),
        message_id=_identifier(message.get("message_id"), "message.message_id"),
        thread_id=None if thread is None else _identifier(thread, "message_thread_id"),
    )


def _reaction_target(raw: Mapping[str, Any]) -> TargetRef:
    chat = _mapping(raw.get("chat"), "reaction.chat")
    return TargetRef(
        channel="telegram",
        chat_id=_identifier(chat.get("id"), "reaction.chat.id"),
        message_id=_identifier(raw.get("message_id"), "reaction.message_id"),
    )


def _reaction_values(value: object, field_name: str) -> tuple[ReactionValue, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(
        _reaction_value(_mapping(item, f"{field_name}[{index}]"))
        for index, item in enumerate(value)
    )


def _reaction_value(value: Mapping[str, Any]) -> ReactionValue:
    reaction_type = ReactionType(value.get("type"))
    if reaction_type is ReactionType.EMOJI:
        return ReactionValue(reaction_type, _text(value.get("emoji"), "reaction.emoji"))
    if reaction_type is ReactionType.CUSTOM_EMOJI:
        return ReactionValue(
            reaction_type,
            _text(value.get("custom_emoji_id"), "reaction.custom_emoji_id"),
        )
    return ReactionValue(reaction_type)


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _identifier(value: object, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{field_name} must be a string or integer")
    return str(value)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _optional_boolean(value: object, field_name: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be bool")
    return value
