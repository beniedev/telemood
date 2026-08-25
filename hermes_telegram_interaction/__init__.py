"""Public, topology-neutral interaction contracts and kernel."""

from .callbacks import CallbackRegistry, CallbackResolution, CallbackStore
from .contracts import (
    CallbackPayload,
    CallbackRejection,
    CallbackToken,
    ChoiceOption,
    ChoicesRequest,
    CompletionMode,
    DeliveryStatus,
    InteractionHost,
    InteractionKind,
    InteractionReceipt,
    MiniAppRequest,
    ReactionRequest,
    StickerPart,
    StickerPartKind,
    StickerRequest,
    TargetRef,
    TransportReceipt,
)
from .kernel import InteractionKernel

__version__ = "0.1.0"

__all__ = [
    "CallbackPayload",
    "CallbackRegistry",
    "CallbackRejection",
    "CallbackStore",
    "CallbackResolution",
    "CallbackToken",
    "ChoiceOption",
    "ChoicesRequest",
    "CompletionMode",
    "DeliveryStatus",
    "InteractionHost",
    "InteractionKernel",
    "InteractionKind",
    "InteractionReceipt",
    "MiniAppRequest",
    "ReactionRequest",
    "StickerPart",
    "StickerPartKind",
    "StickerRequest",
    "TargetRef",
    "TransportReceipt",
]
