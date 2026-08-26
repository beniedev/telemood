"""Interaction orchestration without transport, finalization, or outbox state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable
from uuid import uuid4

from .callbacks import CallbackRegistry, CallbackResolution, CallbackStore
from .contracts import (
    BubbleRequest,
    CallbackPayload,
    CallbackToken,
    ChoicesRequest,
    CompletionMode,
    DeliveryStatus,
    IncomingReaction,
    InteractionCapabilities,
    InteractionHost,
    InteractionKind,
    InteractionReceipt,
    MiniAppRequest,
    ReactionRequest,
    RichReply,
    RichReplyReceipt,
    StickerRequest,
    TargetRef,
    TransportReceipt,
)
from .stickers import StickerCatalog


class InteractionKernel:
    """Turn typed interaction requests into host calls and safe receipts."""

    def __init__(
        self,
        host: InteractionHost,
        *,
        callbacks: CallbackStore | None = None,
        request_id_factory: Callable[[], str] | None = None,
        sticker_catalog: StickerCatalog | None = None,
    ) -> None:
        self._host = host
        if callbacks is not None and not isinstance(callbacks, CallbackStore):
            raise TypeError("callbacks must implement CallbackStore")
        self._callbacks = callbacks or CallbackRegistry()
        self._request_id_factory = request_id_factory or (lambda: uuid4().hex)
        if sticker_catalog is not None and not isinstance(sticker_catalog, StickerCatalog):
            raise TypeError("sticker_catalog must implement StickerCatalog")
        self._sticker_catalog = sticker_catalog

    def send_reaction(
        self,
        request: ReactionRequest,
        *,
        request_id: str | None = None,
        capabilities: InteractionCapabilities | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        if capabilities is not None:
            if not isinstance(capabilities, InteractionCapabilities):
                raise TypeError("capabilities must be InteractionCapabilities")
            if not capabilities.can_send_emoji(request.emoji):
                return self._receipt(
                    request_id=request_id,
                    kind=InteractionKind.REACTION,
                    transport=TransportReceipt(
                        DeliveryStatus.FAILED,
                        detail="reaction_capability_unavailable",
                    ),
                    completion_mode=CompletionMode.NONBLOCKING,
                )
        transport = self._call_single(
            lambda: self._host.send_reaction(request_id, request)
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.REACTION,
            transport=transport,
            completion_mode=CompletionMode.NONBLOCKING,
        )

    def send_bubble(
        self,
        request: BubbleRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        transport = self._call_single(
            lambda: self._host.send_bubble(request_id, request)
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.BUBBLE,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
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
                    **self._thread_kwargs(request.callback_thread_id),
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
        send_miniapp = getattr(self._host, "send_miniapp", None)
        if not callable(send_miniapp):
            return self._receipt(
                request_id=request_id,
                kind=InteractionKind.MINIAPP,
                transport=TransportReceipt(
                    DeliveryStatus.FAILED,
                    detail="miniapp_capability_unavailable",
                ),
                completion_mode=CompletionMode.BLOCKING,
            )
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
                **self._thread_kwargs(request.callback_thread_id),
            )
            token = callback_token
            transport = self._call_single(
                lambda: send_miniapp(request_id, request, callback_token)
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
        thread_id: str | None = None,
    ) -> CallbackResolution:
        try:
            return self._callbacks.consume(
                token,
                user_id=user_id,
                chat_id=chat_id,
                **self._thread_kwargs(thread_id),
            )
        except TypeError:
            if thread_id is not None:
                from .contracts import CallbackRejection

                return CallbackResolution(False, CallbackRejection.THREAD_MISMATCH)
            return self._callbacks.consume(token, user_id=user_id, chat_id=chat_id)

    def execute_reply(
        self,
        reply: RichReply,
        *,
        request_id: str | None = None,
        capabilities: InteractionCapabilities | None = None,
    ) -> RichReplyReceipt:
        if not isinstance(reply, RichReply):
            raise TypeError("reply must be RichReply")
        root_request_id = self._resolve_request_id(request_id)
        receipts: list[InteractionReceipt] = []
        stopped_at: int | None = None
        for index, action in enumerate(reply.actions):
            action_request_id = self._derive_request_id(root_request_id, index)
            if isinstance(action, BubbleRequest):
                receipt = self.send_bubble(action, request_id=action_request_id)
            elif isinstance(action, ReactionRequest):
                receipt = self.send_reaction(
                    action,
                    request_id=action_request_id,
                    capabilities=capabilities,
                )
            elif isinstance(action, StickerRequest):
                receipt = self.send_sticker(action, request_id=action_request_id)
            elif isinstance(action, ChoicesRequest):
                receipt = self.send_choices(action, request_id=action_request_id)
            else:
                raise TypeError("unsupported rich reply action")
            receipts.append(receipt)
            if receipt.status is not DeliveryStatus.VERIFIED:
                stopped_at = index
                break

        completed = len(receipts) == reply.total_actions and all(
            receipt.status is DeliveryStatus.VERIFIED for receipt in receipts
        )
        return RichReplyReceipt(
            request_id=root_request_id,
            total_actions=reply.total_actions,
            receipts=tuple(receipts),
            completed=completed,
            stopped_at=stopped_at,
            verified_visible_completion=completed
            and any(receipt.verified_visible_completion for receipt in receipts),
        )

    def send_seen_sticker(
        self,
        target: TargetRef,
        bot_namespace: str,
        file_unique_id: str,
        *,
        text_before: str | None = None,
        text_after: str | None = None,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        if self._sticker_catalog is None:
            return self._receipt(
                request_id=request_id,
                kind=InteractionKind.STICKER,
                transport=TransportReceipt(
                    DeliveryStatus.FAILED,
                    detail="sticker_catalog_unavailable",
                ),
                completion_mode=CompletionMode.BLOCKING,
            )
        sticker = self._sticker_catalog.get(bot_namespace, file_unique_id)
        if sticker is None:
            return self._receipt(
                request_id=request_id,
                kind=InteractionKind.STICKER,
                transport=TransportReceipt(
                    DeliveryStatus.FAILED,
                    detail="sticker_not_seen_in_bot_namespace",
                ),
                completion_mode=CompletionMode.BLOCKING,
            )
        return self.send_sticker(
            StickerRequest(
                target=target,
                sticker_ref=sticker.file_id,
                text_before=text_before,
                text_after=text_after,
            ),
            request_id=request_id,
        )

    @staticmethod
    def accept_incoming_reaction(
        reaction: IncomingReaction,
        capabilities: InteractionCapabilities,
    ) -> IncomingReaction | None:
        if not isinstance(reaction, IncomingReaction):
            raise TypeError("reaction must be IncomingReaction")
        if not isinstance(capabilities, InteractionCapabilities):
            raise TypeError("capabilities must be InteractionCapabilities")
        if not capabilities.can_receive_reactions or not reaction.is_user_event:
            return None
        return reaction

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

    @staticmethod
    def _derive_request_id(root_request_id: str, index: int) -> str:
        return f"{root_request_id}:{index}"

    @staticmethod
    def _thread_kwargs(thread_id: str | None) -> dict[str, str]:
        return {} if thread_id is None else {"thread_id": thread_id}

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
