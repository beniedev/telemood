"""Topology-neutral contracts for visible Telegram-style interactions.

This module deliberately contains no provider SDK types.  A host adapter owns
transport and returns a small, explicit receipt to the interaction kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Mapping, Protocol, Sequence, runtime_checkable


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or None")
    if value and any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _logical_ref(value: str, field_name: str) -> str:
    value = _required_text(value, field_name)
    if "://" in value:
        raise ValueError(f"{field_name} must be a logical reference, not an endpoint")
    return value


def _positive_finite(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
    if float(value) <= 0:
        raise ValueError(f"{field_name} must be positive")
    return float(value)


class InteractionKind(str, Enum):
    BUBBLE = "bubble"
    REACTION = "reaction"
    CHOICES = "choices"
    STICKER = "sticker"


class DeliveryStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


class CompletionMode(str, Enum):
    NONE = "none"
    NONBLOCKING = "nonblocking"
    BLOCKING = "blocking"


class CallbackRejection(str, Enum):
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    USER_MISMATCH = "user_mismatch"
    CHAT_MISMATCH = "chat_mismatch"
    REPLAY = "replay"
    REVOKED = "revoked"
    PENDING = "pending"
    THREAD_MISMATCH = "thread_mismatch"


class ReactionRejection(str, Enum):
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    UPDATES_NOT_SUBSCRIBED = "updates_not_subscribed"
    BOT_GENERATED = "bot_generated"
    UNSUPPORTED_REACTION_TYPE = "unsupported_reaction_type"


@dataclass(frozen=True)
class TargetRef:
    """Opaque host target; it contains identifiers, never an endpoint."""

    channel: str
    chat_id: str
    message_id: str | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.channel, "channel")
        _required_text(self.chat_id, "chat_id")
        _optional_text(self.message_id, "message_id")
        _optional_text(self.thread_id, "thread_id")


@dataclass(frozen=True)
class ReactionRequest:
    target: TargetRef
    emoji: str

    def __post_init__(self) -> None:
        if self.target.message_id is None:
            raise ValueError("reaction target must include message_id")
        _required_text(self.emoji, "emoji")
        if len(self.emoji) > 32:
            raise ValueError("emoji is too long")


@dataclass(frozen=True)
class BubbleRequest:
    """A single already-delimited semantic message bubble."""

    target: TargetRef
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text or not self.text.strip():
            raise ValueError("bubble text must be non-empty")
        if any(ord(character) < 32 for character in self.text if character not in "\n\r\t"):
            raise ValueError("bubble text must not contain control characters")
        if len(self.text) > 4096:
            raise ValueError("bubble text exceeds Telegram message limit")


@dataclass(frozen=True)
class InteractionCapabilities:
    """Capabilities reported by a host adapter, without provider types."""

    can_send_reactions: bool = False
    can_receive_reaction_changes: bool = False
    can_receive_reaction_counts: bool = False
    message_reaction_subscribed: bool = False
    message_reaction_count_subscribed: bool = False
    available_reactions: tuple[str, ...] | None = None
    reaction_unavailable_reason: str | None = None
    reaction_change_unavailable_reason: str | None = None
    reaction_count_unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "can_send_reactions",
            "can_receive_reaction_changes",
            "can_receive_reaction_counts",
            "message_reaction_subscribed",
            "message_reaction_count_subscribed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be bool")
        if self.available_reactions is not None:
            values = tuple(self.available_reactions)
            if not all(isinstance(value, str) and value for value in values):
                raise ValueError("available_reactions must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError("available_reactions must be unique")
            object.__setattr__(self, "available_reactions", values)
        _optional_text(self.reaction_unavailable_reason, "reaction_unavailable_reason")
        _optional_text(
            self.reaction_change_unavailable_reason,
            "reaction_change_unavailable_reason",
        )
        _optional_text(
            self.reaction_count_unavailable_reason,
            "reaction_count_unavailable_reason",
        )

    @property
    def inbound_reactions_available(self) -> bool:
        return (
            self.can_receive_reaction_changes and self.message_reaction_subscribed
        ) or (
            self.can_receive_reaction_counts
            and self.message_reaction_count_subscribed
        )

    def can_send_emoji(self, emoji: str) -> bool:
        if not self.can_send_reactions:
            return False
        return self.available_reactions is None or emoji in self.available_reactions


class ReactionType(str, Enum):
    EMOJI = "emoji"
    CUSTOM_EMOJI = "custom_emoji"
    PAID = "paid"


@dataclass(frozen=True)
class ReactionValue:
    """Provider-neutral Telegram reaction value."""

    type: ReactionType
    value: str | None = None

    def __post_init__(self) -> None:
        try:
            reaction_type = ReactionType(self.type)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid reaction type") from exc
        object.__setattr__(self, "type", reaction_type)
        if reaction_type is ReactionType.PAID:
            if self.value is not None:
                raise ValueError("paid reaction must not contain a value")
        else:
            _required_text(self.value, "reaction value")


@dataclass(frozen=True)
class ReactionActor:
    """Exactly one concrete user or anonymous actor chat."""

    user_id: str | None = None
    chat_id: str | None = None

    def __post_init__(self) -> None:
        if (self.user_id is None) == (self.chat_id is None):
            raise ValueError("reaction actor requires exactly one user_id or chat_id")
        _optional_text(self.user_id, "reaction actor user_id")
        _optional_text(self.chat_id, "reaction actor chat_id")


@dataclass(frozen=True)
class IncomingReactionChange:
    """A Telegram reaction change with actor and old/new sets preserved."""

    target: TargetRef
    actor: ReactionActor
    old_reactions: tuple[ReactionValue, ...]
    new_reactions: tuple[ReactionValue, ...]
    changed_at: int
    bot_generated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetRef) or self.target.message_id is None:
            raise ValueError("reaction change target must include message_id")
        if not isinstance(self.actor, ReactionActor):
            raise ValueError("reaction change actor must be ReactionActor")
        old_reactions = tuple(self.old_reactions)
        new_reactions = tuple(self.new_reactions)
        if not all(isinstance(value, ReactionValue) for value in old_reactions + new_reactions):
            raise ValueError("reaction sets must contain ReactionValue values")
        if not isinstance(self.changed_at, int) or isinstance(self.changed_at, bool) or self.changed_at < 0:
            raise ValueError("changed_at must be a non-negative integer timestamp")
        if not isinstance(self.bot_generated, bool):
            raise ValueError("bot_generated must be bool")
        object.__setattr__(self, "old_reactions", old_reactions)
        object.__setattr__(self, "new_reactions", new_reactions)


@dataclass(frozen=True)
class ReactionCount:
    """One aggregate reaction count without a user actor."""

    reaction: ReactionValue
    total_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.reaction, ReactionValue):
            raise ValueError("reaction must be ReactionValue")
        if not isinstance(self.total_count, int) or isinstance(self.total_count, bool) or self.total_count < 0:
            raise ValueError("total_count must be a non-negative integer")


@dataclass(frozen=True)
class IncomingReactionCount:
    """An anonymous aggregate update, which Telegram may deliver with delay."""

    target: TargetRef
    counts: tuple[ReactionCount, ...]
    changed_at: int
    delayed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetRef) or self.target.message_id is None:
            raise ValueError("reaction count target must include message_id")
        counts = tuple(self.counts)
        if not all(isinstance(value, ReactionCount) for value in counts):
            raise ValueError("counts must contain ReactionCount values")
        if not isinstance(self.changed_at, int) or isinstance(self.changed_at, bool) or self.changed_at < 0:
            raise ValueError("changed_at must be a non-negative integer timestamp")
        if not isinstance(self.delayed, bool):
            raise ValueError("delayed must be bool")
        object.__setattr__(self, "counts", counts)


ReactionEvent = IncomingReactionChange | IncomingReactionCount


@dataclass(frozen=True)
class ReactionAcceptance:
    """Explicit normalization result for supported or rejected inbound updates."""

    accepted: bool
    event: ReactionEvent | None = None
    reason: ReactionRejection | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be bool")
        if self.accepted:
            if not isinstance(self.event, (IncomingReactionChange, IncomingReactionCount)):
                raise ValueError("accepted reaction must contain an event")
            if self.reason is not None:
                raise ValueError("accepted reaction cannot contain a rejection reason")
            if self.detail is not None:
                raise ValueError("accepted reaction cannot contain rejection detail")
        else:
            if self.event is not None:
                raise ValueError("rejected reaction cannot contain an event")
            try:
                reason = ReactionRejection(self.reason)
            except (TypeError, ValueError) as exc:
                raise ValueError("rejected reaction requires a valid reason") from exc
            object.__setattr__(self, "reason", reason)
            _optional_text(self.detail, "reaction rejection detail")


@dataclass(frozen=True)
class ChoiceOption:
    key: str
    label: str

    def __post_init__(self) -> None:
        _required_text(self.key, "choice key")
        _required_text(self.label, "choice label")


@dataclass(frozen=True)
class ChoicesRequest:
    target: TargetRef
    prompt: str
    options: tuple[ChoiceOption, ...]
    authorized_user_id: str
    callback_ttl_seconds: float = 1800.0
    authorized_thread_id: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.prompt, "prompt")
        _required_text(self.authorized_user_id, "authorized_user_id")
        _optional_text(self.authorized_thread_id, "authorized_thread_id")
        if (
            self.authorized_thread_id is not None
            and self.target.thread_id is not None
            and self.authorized_thread_id != self.target.thread_id
        ):
            raise ValueError("authorized_thread_id must match target.thread_id")
        options = tuple(self.options)
        if not 2 <= len(options) <= 4:
            raise ValueError("choices must contain between two and four options")
        if not all(isinstance(option, ChoiceOption) for option in options):
            raise ValueError("options must contain ChoiceOption values")
        if len({option.key for option in options}) != len(options):
            raise ValueError("choice keys must be unique")
        object.__setattr__(self, "options", options)
        object.__setattr__(
            self,
            "callback_ttl_seconds",
            _positive_finite(self.callback_ttl_seconds, "callback_ttl_seconds"),
        )

    @property
    def callback_thread_id(self) -> str | None:
        return self.authorized_thread_id or self.target.thread_id


class StickerPartKind(str, Enum):
    TEXT = "text"
    STICKER = "sticker"


class StickerFormat(str, Enum):
    STATIC = "static"
    ANIMATED = "animated"
    VIDEO = "video"


class StickerType(str, Enum):
    REGULAR = "regular"
    MASK = "mask"
    CUSTOM_EMOJI = "custom_emoji"


@dataclass(frozen=True)
class RegularSticker:
    """Bot-scoped regular sticker metadata, independent of a Telegram SDK."""

    bot_namespace: str
    file_id: str
    file_unique_id: str
    catalog_id: str | None = None
    emoji: str | None = None
    set_name: str | None = None
    format: StickerFormat = StickerFormat.STATIC
    thumbnail_ref: str | None = None
    media_ref: str | None = None
    type: StickerType = StickerType.REGULAR

    def __post_init__(self) -> None:
        _required_text(self.bot_namespace, "bot_namespace")
        _logical_ref(self.file_id, "file_id")
        _logical_ref(self.file_unique_id, "file_unique_id")
        if self.catalog_id is not None:
            _logical_ref(self.catalog_id, "catalog_id")
        try:
            sticker_type = StickerType(self.type)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sticker type") from exc
        if sticker_type is not StickerType.REGULAR:
            raise ValueError("only regular stickers can be stored for v0.1")
        object.__setattr__(self, "type", sticker_type)
        try:
            sticker_format = StickerFormat(self.format)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sticker format") from exc
        object.__setattr__(self, "format", sticker_format)
        _optional_text(self.emoji, "sticker emoji")
        _optional_text(self.set_name, "sticker set_name")
        if self.thumbnail_ref is not None:
            _logical_ref(self.thumbnail_ref, "thumbnail_ref")
        if self.media_ref is not None:
            _logical_ref(self.media_ref, "media_ref")


@dataclass(frozen=True)
class IncomingSticker:
    """Normalized Telegram sticker metadata supplied by a host adapter."""

    bot_namespace: str
    file_id: str
    file_unique_id: str
    type: StickerType = StickerType.REGULAR
    emoji: str | None = None
    set_name: str | None = None
    format: StickerFormat = StickerFormat.STATIC
    thumbnail_ref: str | None = None
    media_ref: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.bot_namespace, "bot_namespace")
        _logical_ref(self.file_id, "file_id")
        _logical_ref(self.file_unique_id, "file_unique_id")
        try:
            sticker_type = StickerType(self.type)
            sticker_format = StickerFormat(self.format)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sticker type or format") from exc
        object.__setattr__(self, "type", sticker_type)
        object.__setattr__(self, "format", sticker_format)
        _optional_text(self.emoji, "sticker emoji")
        _optional_text(self.set_name, "sticker set_name")
        if self.thumbnail_ref is not None:
            _logical_ref(self.thumbnail_ref, "thumbnail_ref")
        if self.media_ref is not None:
            _logical_ref(self.media_ref, "media_ref")

    def as_regular(self) -> RegularSticker:
        if self.type is not StickerType.REGULAR:
            raise ValueError("only regular stickers can be stored for v0.1")
        return RegularSticker(
            bot_namespace=self.bot_namespace,
            file_id=self.file_id,
            file_unique_id=self.file_unique_id,
            emoji=self.emoji,
            set_name=self.set_name,
            format=self.format,
            thumbnail_ref=self.thumbnail_ref,
            media_ref=self.media_ref,
            type=self.type,
        )


@dataclass(frozen=True)
class IncomingStickerEvent:
    """Inbound sticker plus trusted message, sender, and time context."""

    target: TargetRef
    sticker: IncomingSticker
    received_at: int
    sender_user_id: str | None = None
    sender_chat_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, TargetRef) or self.target.message_id is None:
            raise ValueError("incoming sticker target must include message_id")
        if not isinstance(self.sticker, IncomingSticker):
            raise ValueError("sticker must be IncomingSticker")
        if not isinstance(self.received_at, int) or isinstance(self.received_at, bool) or self.received_at < 0:
            raise ValueError("received_at must be a non-negative integer timestamp")
        if (self.sender_user_id is None) == (self.sender_chat_id is None):
            raise ValueError("incoming sticker requires exactly one sender identity")
        _optional_text(self.sender_user_id, "sender_user_id")
        _optional_text(self.sender_chat_id, "sender_chat_id")


@dataclass(frozen=True)
class StickerModelView:
    """Safe model-facing sticker view; reusable Telegram file ids are omitted."""

    catalog_id: str
    text: str
    thumbnail_ref: str | None = None
    media_ref: str | None = None

    def __post_init__(self) -> None:
        _logical_ref(self.catalog_id, "catalog_id")
        _required_text(self.text, "sticker model text")
        if self.thumbnail_ref is not None:
            _logical_ref(self.thumbnail_ref, "thumbnail_ref")
        if self.media_ref is not None:
            _logical_ref(self.media_ref, "media_ref")


class StickerSenderKind(str, Enum):
    USER = "user"
    CHAT = "chat"


@dataclass(frozen=True)
class StickerModelEvent:
    """Safe sticker projection with context but without provider identifiers."""

    sticker: StickerModelView
    sender_kind: StickerSenderKind
    target_role: str
    in_thread: bool
    occurred_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.sticker, StickerModelView):
            raise ValueError("sticker must be StickerModelView")
        try:
            sender_kind = StickerSenderKind(self.sender_kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sticker sender kind") from exc
        object.__setattr__(self, "sender_kind", sender_kind)
        _required_text(self.target_role, "target_role")
        if not isinstance(self.in_thread, bool):
            raise ValueError("in_thread must be bool")
        if (
            not isinstance(self.occurred_at, int)
            or isinstance(self.occurred_at, bool)
            or self.occurred_at < 0
        ):
            raise ValueError("occurred_at must be a non-negative integer timestamp")


@dataclass(frozen=True)
class StickerPart:
    kind: StickerPartKind
    value: str

    def __post_init__(self) -> None:
        try:
            kind = StickerPartKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid sticker part kind") from exc
        object.__setattr__(self, "kind", kind)
        _required_text(self.value, "sticker part value")


@dataclass(frozen=True)
class StickerRequest:
    target: TargetRef
    sticker_ref: str
    text_before: str | None = None
    text_after: str | None = None

    def __post_init__(self) -> None:
        _logical_ref(self.sticker_ref, "sticker_ref")
        _optional_text(self.text_before, "text_before")
        _optional_text(self.text_after, "text_after")

    @property
    def parts(self) -> tuple[StickerPart, ...]:
        parts: list[StickerPart] = []
        if self.text_before:
            parts.append(StickerPart(StickerPartKind.TEXT, self.text_before))
        parts.append(StickerPart(StickerPartKind.STICKER, self.sticker_ref))
        if self.text_after:
            parts.append(StickerPart(StickerPartKind.TEXT, self.text_after))
        return tuple(parts)


RichAction = BubbleRequest | ReactionRequest | StickerRequest | ChoicesRequest


@dataclass(frozen=True)
class RichReply:
    """An ordered, non-empty sequence of v0.1 rich actions."""

    actions: tuple[RichAction, ...]

    def __post_init__(self) -> None:
        actions = tuple(self.actions)
        if not actions:
            raise ValueError("rich reply must contain at least one action")
        if not all(
            isinstance(action, (BubbleRequest, ReactionRequest, StickerRequest, ChoicesRequest))
            for action in actions
        ):
            raise ValueError("rich reply contains an unsupported action")
        object.__setattr__(self, "actions", actions)

    @property
    def total_actions(self) -> int:
        return len(self.actions)


@dataclass(frozen=True)
class CallbackToken:
    """Opaque handle with optional Unix expiry metadata for host-owned UX."""

    value: str
    expires_at: float | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        _required_text(self.value, "callback token")
        if len(self.value.encode("utf-8")) > 64:
            raise ValueError("callback token exceeds Telegram's 64-byte limit")
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                _positive_finite(self.expires_at, "callback expiry"),
            )


@dataclass(frozen=True)
class CallbackPayload:
    kind: InteractionKind
    request_id: str
    value: str

    def __post_init__(self) -> None:
        try:
            kind = InteractionKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid callback kind") from exc
        object.__setattr__(self, "kind", kind)
        _required_text(self.request_id, "request_id")
        _required_text(self.value, "callback value")


@dataclass(frozen=True)
class TransportReceipt:
    """The only transport result accepted by the kernel."""

    status: DeliveryStatus
    provider_delivery_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        try:
            status = DeliveryStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid delivery status") from exc
        object.__setattr__(self, "status", status)
        _optional_text(self.provider_delivery_id, "provider_delivery_id")
        _optional_text(self.detail, "detail")


@dataclass(frozen=True)
class InteractionReceipt:
    request_id: str
    kind: InteractionKind
    status: DeliveryStatus
    completion_mode: CompletionMode
    verified_visible_completion: bool
    provider_delivery_id: str | None = None
    callback_tokens: tuple[CallbackToken, ...] = ()
    detail: str | None = None
    part_receipts: tuple[TransportReceipt, ...] = ()
    total_parts: int = 0

    def __post_init__(self) -> None:
        _required_text(self.request_id, "request_id")
        try:
            kind = InteractionKind(self.kind)
            status = DeliveryStatus(self.status)
            completion_mode = CompletionMode(self.completion_mode)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid interaction receipt enum") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "completion_mode", completion_mode)
        object.__setattr__(self, "callback_tokens", tuple(self.callback_tokens))
        part_receipts = tuple(self.part_receipts)
        if not all(isinstance(value, TransportReceipt) for value in part_receipts):
            raise ValueError("part_receipts must contain TransportReceipt values")
        if not isinstance(self.total_parts, int) or isinstance(self.total_parts, bool) or self.total_parts < 0:
            raise ValueError("total_parts must be a non-negative integer")
        if len(part_receipts) > self.total_parts:
            raise ValueError("part receipt count cannot exceed total_parts")
        object.__setattr__(self, "part_receipts", part_receipts)
        _optional_text(self.provider_delivery_id, "provider_delivery_id")
        _optional_text(self.detail, "detail")

        if kind is InteractionKind.REACTION and completion_mode is not CompletionMode.NONBLOCKING:
            raise ValueError("reaction receipts must be nonblocking")
        if status is not DeliveryStatus.VERIFIED and self.verified_visible_completion:
            raise ValueError("unverified transport cannot have visible completion")
        if completion_mode is not CompletionMode.BLOCKING and self.verified_visible_completion:
            raise ValueError("only blocking interactions can complete a visible turn")
        if status is not DeliveryStatus.VERIFIED and self.callback_tokens:
            raise ValueError("unverified transport cannot return active callback tokens")

    @property
    def part_statuses(self) -> tuple[DeliveryStatus, ...]:
        return tuple(receipt.status for receipt in self.part_receipts)

    @property
    def unexecuted_parts(self) -> int:
        return self.total_parts - len(self.part_receipts)

    @property
    def callback_expires_at(self) -> float | None:
        """Earliest absolute expiry exposed by the active callback handles."""
        if not self.callback_tokens:
            return None
        deadlines = tuple(token.expires_at for token in self.callback_tokens)
        if any(deadline is None for deadline in deadlines):
            return None
        return min(deadline for deadline in deadlines if deadline is not None)


@dataclass(frozen=True)
class RichReplyReceipt:
    """Ordered receipts for a rich reply, including the stop boundary."""

    request_id: str
    total_actions: int
    receipts: tuple[InteractionReceipt, ...]
    completed: bool
    stopped_at: int | None
    verified_visible_completion: bool

    def __post_init__(self) -> None:
        _required_text(self.request_id, "request_id")
        if not isinstance(self.total_actions, int) or self.total_actions <= 0:
            raise ValueError("total_actions must be positive")
        receipts = tuple(self.receipts)
        if len(receipts) > self.total_actions:
            raise ValueError("receipt count cannot exceed total_actions")
        if not all(isinstance(receipt, InteractionReceipt) for receipt in receipts):
            raise ValueError("receipts must contain InteractionReceipt values")
        object.__setattr__(self, "receipts", receipts)
        if self.stopped_at is not None and not (
            0 <= self.stopped_at < self.total_actions
        ):
            raise ValueError("stopped_at must identify an action in the reply")
        expected_completed = len(receipts) == self.total_actions and all(
            receipt.status is DeliveryStatus.VERIFIED for receipt in receipts
        )
        if self.completed != expected_completed:
            raise ValueError("completed does not match action receipts")
        if self.completed and self.stopped_at is not None:
            raise ValueError("completed reply cannot have stopped_at")
        if not self.completed and self.stopped_at is None:
            raise ValueError("incomplete reply must have stopped_at")
        if self.verified_visible_completion and not (
            self.completed
            and any(receipt.verified_visible_completion for receipt in receipts)
        ):
            raise ValueError("visible completion requires a completed visible action")

    @property
    def action_receipts(self) -> tuple[InteractionReceipt, ...]:
        return self.receipts

    @property
    def unexecuted_count(self) -> int:
        return self.total_actions - len(self.receipts)


@runtime_checkable
class InteractionHost(Protocol):
    """Injected host adapter protocol with a caller-stable correlation id.

    ``request_id`` must be forwarded by the host to its transport operation;
    Telegram does not treat it as a universal idempotency key, and this
    protocol does not create a second queue or delivery ledger.
    """

    def send_reaction(
        self,
        request_id: str,
        request: ReactionRequest,
    ) -> TransportReceipt:
        ...

    def send_bubble(
        self,
        request_id: str,
        request: BubbleRequest,
    ) -> TransportReceipt:
        ...

    def send_choices(
        self,
        request_id: str,
        request: ChoicesRequest,
        callback_tokens: Mapping[str, CallbackToken],
    ) -> TransportReceipt:
        ...

    def send_sticker_sequence(
        self,
        request_id: str,
        request: StickerRequest,
        parts: Sequence[StickerPart],
    ) -> Sequence[TransportReceipt]:
        """Send in order, stop on non-verified, and return attempted receipts."""
        ...


@runtime_checkable
class AsyncInteractionHost(Protocol):
    """Async counterpart for hosts that already own an event loop."""

    async def send_reaction(
        self,
        request_id: str,
        request: ReactionRequest,
    ) -> TransportReceipt:
        ...

    async def send_bubble(
        self,
        request_id: str,
        request: BubbleRequest,
    ) -> TransportReceipt:
        ...

    async def send_choices(
        self,
        request_id: str,
        request: ChoicesRequest,
        callback_tokens: Mapping[str, CallbackToken],
    ) -> TransportReceipt:
        ...

    async def send_sticker_sequence(
        self,
        request_id: str,
        request: StickerRequest,
        parts: Sequence[StickerPart],
    ) -> Sequence[TransportReceipt]:
        """Send in order, stop on non-verified, and return attempted receipts."""
        ...
