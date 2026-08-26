"""Topology-neutral contracts for visible Telegram-style interactions.

This module deliberately contains no provider SDK types.  A host adapter owns
transport and returns a small, explicit receipt to the interaction kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    MINIAPP = "miniapp"


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

    can_send_reactions: bool = True
    can_receive_reactions: bool = False
    available_reactions: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.can_send_reactions, bool):
            raise ValueError("can_send_reactions must be bool")
        if not isinstance(self.can_receive_reactions, bool):
            raise ValueError("can_receive_reactions must be bool")
        if self.available_reactions is not None:
            values = tuple(self.available_reactions)
            if not all(isinstance(value, str) and value for value in values):
                raise ValueError("available_reactions must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError("available_reactions must be unique")
            object.__setattr__(self, "available_reactions", values)

    @property
    def inbound_reactions_available(self) -> bool:
        return self.can_receive_reactions

    def can_send_emoji(self, emoji: str) -> bool:
        if not self.can_send_reactions:
            return False
        return self.available_reactions is None or emoji in self.available_reactions


@dataclass(frozen=True)
class IncomingReaction:
    """Normalized reaction input; unavailable and bot-generated states are explicit."""

    target: TargetRef | None
    emoji: str | None
    user_id: str | None
    bot_generated: bool = False
    available: bool = True
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.available:
            if self.target is None or self.emoji is None or self.user_id is None:
                raise ValueError("available reaction requires target, emoji, and user_id")
            _required_text(self.emoji, "reaction emoji")
            _required_text(self.user_id, "reaction user_id")
        else:
            if self.emoji is not None or self.user_id is not None:
                raise ValueError("unavailable reaction cannot contain user event data")
        if not isinstance(self.bot_generated, bool) or not isinstance(self.available, bool):
            raise ValueError("reaction state flags must be bool")
        _optional_text(self.detail, "reaction detail")

    @classmethod
    def unavailable(cls, detail: str | None = None) -> "IncomingReaction":
        return cls(target=None, emoji=None, user_id=None, available=False, detail=detail)

    @property
    def is_user_event(self) -> bool:
        return self.available and not self.bot_generated


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


@dataclass(frozen=True)
class RegularSticker:
    """Bot-scoped regular sticker metadata, independent of a Telegram SDK."""

    bot_namespace: str
    file_id: str
    file_unique_id: str
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
    """A normalized regular sticker update supplied by a host adapter."""

    bot_namespace: str
    file_id: str
    file_unique_id: str
    emoji: str | None = None
    set_name: str | None = None
    format: StickerFormat = StickerFormat.STATIC
    thumbnail_ref: str | None = None
    media_ref: str | None = None

    def __post_init__(self) -> None:
        regular = RegularSticker(
            bot_namespace=self.bot_namespace,
            file_id=self.file_id,
            file_unique_id=self.file_unique_id,
            emoji=self.emoji,
            set_name=self.set_name,
            format=self.format,
            thumbnail_ref=self.thumbnail_ref,
            media_ref=self.media_ref,
        )
        for field in (
            "bot_namespace",
            "file_id",
            "file_unique_id",
            "emoji",
            "set_name",
            "format",
            "thumbnail_ref",
            "media_ref",
        ):
            object.__setattr__(self, field, getattr(regular, field))

    def as_regular(self) -> RegularSticker:
        return RegularSticker(
            bot_namespace=self.bot_namespace,
            file_id=self.file_id,
            file_unique_id=self.file_unique_id,
            emoji=self.emoji,
            set_name=self.set_name,
            format=self.format,
            thumbnail_ref=self.thumbnail_ref,
            media_ref=self.media_ref,
        )


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


@dataclass(frozen=True)
class MiniAppRequest:
    target: TargetRef
    app_ref: str
    button_label: str
    authorized_user_id: str
    callback_ttl_seconds: float = 1800.0
    authorized_thread_id: str | None = None

    def __post_init__(self) -> None:
        _logical_ref(self.app_ref, "app_ref")
        _required_text(self.button_label, "button_label")
        _required_text(self.authorized_user_id, "authorized_user_id")
        _optional_text(self.authorized_thread_id, "authorized_thread_id")
        if (
            self.authorized_thread_id is not None
            and self.target.thread_id is not None
            and self.authorized_thread_id != self.target.thread_id
        ):
            raise ValueError("authorized_thread_id must match target.thread_id")
        object.__setattr__(
            self,
            "callback_ttl_seconds",
            _positive_finite(self.callback_ttl_seconds, "callback_ttl_seconds"),
        )

    @property
    def callback_thread_id(self) -> str | None:
        return self.authorized_thread_id or self.target.thread_id


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
    """Opaque callback handle.  The host decides how it reaches a user."""

    value: str

    def __post_init__(self) -> None:
        _required_text(self.value, "callback token")


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
    part_statuses: tuple[DeliveryStatus, ...] = ()

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
        part_statuses = tuple(DeliveryStatus(value) for value in self.part_statuses)
        object.__setattr__(self, "part_statuses", part_statuses)
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
    """Injected host adapter protocol with a caller-stable idempotency key.

    ``request_id`` must be forwarded by the host to its transport operation;
    this protocol does not create a second queue or delivery ledger.
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
        ...

@runtime_checkable
class MiniAppHost(Protocol):
    """Optional legacy mini-app adapter; not required by the v0.1 core."""

    def send_miniapp(
        self,
        request_id: str,
        request: MiniAppRequest,
        callback_token: CallbackToken,
    ) -> TransportReceipt:
        ...
