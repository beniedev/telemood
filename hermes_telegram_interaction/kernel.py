"""Interaction orchestration without transport, finalization, or outbox state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable
from uuid import uuid4

from .callbacks import CallbackRegistry, CallbackResolution, CallbackStore
from .contracts import (
    CallbackPayload,
    CallbackToken,
    ChoicesRequest,
    CompletionMode,
    DeliveryStatus,
    InteractionHost,
    InteractionKind,
    InteractionReceipt,
    MiniAppRequest,
    ReactionRequest,
    StickerRequest,
    TransportReceipt,
)


class InteractionKernel:
    """Turn typed interaction requests into host calls and safe receipts."""

    def __init__(
        self,
        host: InteractionHost,
        *,
        callbacks: CallbackStore | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._host = host
        if callbacks is not None and not isinstance(callbacks, CallbackStore):
            raise TypeError("callbacks must implement CallbackStore")
        self._callbacks = callbacks or CallbackRegistry()
        self._request_id_factory = request_id_factory or (lambda: uuid4().hex)

    def send_reaction(
        self,
        request: ReactionRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        transport = self._call_single(
            lambda: self._host.send_reaction(request_id, request)
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.REACTION,
            transport=transport,
            completion_mode=CompletionMode.NONBLOCKING,
        )

    def send_choices(
        self,
        request: ChoicesRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        tokens: dict[str, CallbackToken] = {}
        try:
            for option in request.options:
                tokens[option.key] = self._callbacks.register(
                    user_id=request.authorized_user_id,
                    chat_id=request.target.chat_id,
                    payload=CallbackPayload(
                        kind=InteractionKind.CHOICES,
                        request_id=request_id,
                        value=option.key,
                    ),
                    ttl_seconds=request.callback_ttl_seconds,
                )
            transport = self._call_single(
                lambda: self._host.send_choices(request_id, request, tokens)
            )
        except Exception:
            self._revoke(tokens.values())
            transport = TransportReceipt(DeliveryStatus.FAILED, detail="callback_setup_failed")

        transport, active_tokens = self._activate_callbacks(transport, tokens.values())
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.CHOICES,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
            callback_tokens=active_tokens,
        )

    def send_sticker(
        self,
        request: StickerRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        parts = request.parts
        transport_parts = self._call_sequence(
            lambda: self._host.send_sticker_sequence(request_id, request, parts),
            expected_count=len(parts),
        )
        status = self._combine_statuses(transport_parts)
        transport = TransportReceipt(status, detail="sticker_sequence")
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.STICKER,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
            part_statuses=tuple(item.status for item in transport_parts),
        )

    def send_miniapp(
        self,
        request: MiniAppRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        token: CallbackToken | None = None
        try:
            callback_token = self._callbacks.register(
                user_id=request.authorized_user_id,
                chat_id=request.target.chat_id,
                payload=CallbackPayload(
                    kind=InteractionKind.MINIAPP,
                    request_id=request_id,
                    value=request.app_ref,
                ),
                ttl_seconds=request.callback_ttl_seconds,
            )
            token = callback_token
            transport = self._call_single(
                lambda: self._host.send_miniapp(request_id, request, callback_token)
            )
        except Exception:
            if token is not None:
                self._callbacks.revoke(token)
            transport = TransportReceipt(DeliveryStatus.FAILED, detail="callback_setup_failed")

        transport, active_tokens = self._activate_callbacks(
            transport,
            (token,) if token is not None else (),
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.MINIAPP,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
            callback_tokens=active_tokens,
        )

    def consume_callback(
        self,
        token: CallbackToken,
        *,
        user_id: str,
        chat_id: str,
    ) -> CallbackResolution:
        return self._callbacks.consume(token, user_id=user_id, chat_id=chat_id)

    @staticmethod
    def _call_single(call: Callable[[], object]) -> TransportReceipt:
        try:
            result = call()
        except Exception:
            return TransportReceipt(DeliveryStatus.FAILED, detail="host_exception")
        if not isinstance(result, TransportReceipt):
            return TransportReceipt(DeliveryStatus.UNKNOWN, detail="invalid_transport_receipt")
        return result

    @staticmethod
    def _call_sequence(
        call: Callable[[], object],
        *,
        expected_count: int,
    ) -> tuple[TransportReceipt, ...]:
        try:
            result = call()
        except Exception:
            return (TransportReceipt(DeliveryStatus.FAILED, detail="host_exception"),)
        if isinstance(result, (str, bytes)) or not isinstance(result, Sequence):
            return (TransportReceipt(DeliveryStatus.UNKNOWN, detail="invalid_transport_sequence"),)
        receipts = tuple(
            item
            for item in result
            if isinstance(item, TransportReceipt)
        )
        if len(receipts) != len(result):
            return (TransportReceipt(DeliveryStatus.UNKNOWN, detail="invalid_transport_sequence"),)
        if len(receipts) != expected_count:
            return (
                TransportReceipt(
                    DeliveryStatus.UNKNOWN,
                    detail="invalid_transport_sequence_length",
                ),
            )
        return receipts

    @staticmethod
    def _combine_statuses(receipts: Sequence[TransportReceipt]) -> DeliveryStatus:
        statuses = {receipt.status for receipt in receipts}
        if DeliveryStatus.FAILED in statuses:
            return DeliveryStatus.FAILED
        if DeliveryStatus.UNCERTAIN in statuses:
            return DeliveryStatus.UNCERTAIN
        if DeliveryStatus.UNKNOWN in statuses:
            return DeliveryStatus.UNKNOWN
        return DeliveryStatus.VERIFIED

    def _receipt(
        self,
        *,
        request_id: str,
        kind: InteractionKind,
        transport: TransportReceipt,
        completion_mode: CompletionMode,
        callback_tokens: tuple[CallbackToken, ...] = (),
        part_statuses: tuple[DeliveryStatus, ...] = (),
    ) -> InteractionReceipt:
        verified_completion = (
            transport.status is DeliveryStatus.VERIFIED
            and completion_mode is CompletionMode.BLOCKING
        )
        return InteractionReceipt(
            request_id=request_id,
            kind=kind,
            status=transport.status,
            completion_mode=completion_mode,
            verified_visible_completion=verified_completion,
            provider_delivery_id=transport.provider_delivery_id,
            callback_tokens=callback_tokens,
            detail=transport.detail,
            part_statuses=part_statuses,
        )

    def _resolve_request_id(self, request_id: str | None) -> str:
        value = self._request_id_factory() if request_id is None else request_id
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("request_id must be a non-empty trimmed string")
        return value

    def _activate_callbacks(
        self,
        transport: TransportReceipt,
        tokens: Sequence[CallbackToken],
    ) -> tuple[TransportReceipt, tuple[CallbackToken, ...]]:
        if transport.status is not DeliveryStatus.VERIFIED:
            self._revoke(tokens)
            return transport, ()
        if not tokens:
            return transport, ()

        activate_all = getattr(self._callbacks, "activate_all", None)
        if callable(activate_all):
            try:
                activated = bool(activate_all(tuple(tokens)))
            except Exception:
                activated = False
            if activated:
                return transport, tuple(tokens)
            self._revoke(tokens)
            return (
                TransportReceipt(
                    DeliveryStatus.UNCERTAIN,
                    provider_delivery_id=transport.provider_delivery_id,
                    detail="delivered_but_callback_activation_failed",
                ),
                (),
            )

        activate = getattr(self._callbacks, "activate", None)
        if not callable(activate):
            self._revoke(tokens)
            return (
                TransportReceipt(
                    DeliveryStatus.UNCERTAIN,
                    provider_delivery_id=transport.provider_delivery_id,
                    detail="delivered_but_callback_activation_failed",
                ),
                (),
            )
        active_tokens: list[CallbackToken] = []
        for token in tokens:
            try:
                activated = bool(activate(token))
            except Exception:
                activated = False
            if not activated:
                self._revoke(tokens)
                return (
                    TransportReceipt(
                        DeliveryStatus.UNCERTAIN,
                        provider_delivery_id=transport.provider_delivery_id,
                        detail="delivered_but_callback_activation_failed",
                    ),
                    (),
                )
            active_tokens.append(token)
        return transport, tuple(active_tokens)

    def _revoke(self, tokens: Sequence[CallbackToken]) -> None:
        if not tokens:
            return
        revoke_all = getattr(self._callbacks, "revoke_all", None)
        if callable(revoke_all):
            try:
                revoke_all(tuple(tokens))
            except Exception:
                pass
            return
        revoke = getattr(self._callbacks, "revoke", None)
        if not callable(revoke):
            return
        for token in tokens:
            try:
                revoke(token)
            except Exception:
                pass
