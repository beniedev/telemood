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
        _required_text(self.emoji, "emoji")
        if len(self.emoji) > 32:
            raise ValueError("emoji is too long")


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

    def __post_init__(self) -> None:
        _required_text(self.prompt, "prompt")
        _required_text(self.authorized_user_id, "authorized_user_id")
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


class StickerPartKind(str, Enum):
    TEXT = "text"
    STICKER = "sticker"


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

    def __post_init__(self) -> None:
        _logical_ref(self.app_ref, "app_ref")
        _required_text(self.button_label, "button_label")
        _required_text(self.authorized_user_id, "authorized_user_id")
        object.__setattr__(
            self,
            "callback_ttl_seconds",
            _positive_finite(self.callback_ttl_seconds, "callback_ttl_seconds"),
        )


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

    def send_miniapp(
        self,
        request_id: str,
        request: MiniAppRequest,
        callback_token: CallbackToken,
    ) -> TransportReceipt:
        ...
