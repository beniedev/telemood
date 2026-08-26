"""Async interaction kernel for hosts that already own an event loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .contracts import (
    AsyncInteractionHost,
    BubbleRequest,
    CallbackPayload,
    CallbackToken,
    ChoicesRequest,
    CompletionMode,
    DeliveryStatus,
    InteractionCapabilities,
    InteractionKind,
    InteractionReceipt,
    ReactionRequest,
    RichReply,
    RichReplyReceipt,
    StickerRequest,
    TargetRef,
    TransportReceipt,
)
from .kernel import InteractionKernel


class AsyncInteractionKernel(InteractionKernel):
    """Await an injected async host without creating threads or event loops."""

    def __init__(self, host: AsyncInteractionHost, **kwargs) -> None:
        super().__init__(host, **kwargs)
        self._host = host

    async def send_reaction(
        self,
        request: ReactionRequest,
        *,
        request_id: str | None = None,
        capabilities: InteractionCapabilities | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        capabilities = capabilities or InteractionCapabilities()
        if not isinstance(capabilities, InteractionCapabilities):
            raise TypeError("capabilities must be InteractionCapabilities")
        if not capabilities.can_send_emoji(request.emoji):
            return self._receipt(
                request_id=request_id,
                kind=InteractionKind.REACTION,
                transport=TransportReceipt(
                    DeliveryStatus.FAILED,
                    detail=capabilities.reaction_unavailable_reason
                    or "reaction_capability_unavailable",
                ),
                completion_mode=CompletionMode.NONBLOCKING,
            )
        transport = await self._call_single_async(
            lambda: self._host.send_reaction(request_id, request)
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.REACTION,
            transport=transport,
            completion_mode=CompletionMode.NONBLOCKING,
        )

    async def send_bubble(
        self,
        request: BubbleRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        transport = await self._call_single_async(
            lambda: self._host.send_bubble(request_id, request)
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.BUBBLE,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
        )

    async def send_choices(
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
            transport = await self._call_single_async(
                lambda: self._host.send_choices(request_id, request, tokens)
            )
        except Exception:
            self._revoke(tokens.values())
            transport = TransportReceipt(
                DeliveryStatus.FAILED,
                detail="callback_setup_failed",
            )
        transport, active_tokens = self._activate_callbacks(transport, tokens.values())
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.CHOICES,
            transport=transport,
            completion_mode=CompletionMode.BLOCKING,
            callback_tokens=active_tokens,
        )

    async def send_sticker(
        self,
        request: StickerRequest,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        parts = request.parts
        transport_parts, sequence_issue = await self._call_sequence_async(
            lambda: self._host.send_sticker_sequence(request_id, request, parts),
            expected_count=len(parts),
        )
        status = self._combine_statuses(transport_parts)
        if sequence_issue is not None and status is DeliveryStatus.VERIFIED:
            status = DeliveryStatus.UNKNOWN
        last_failure = next(
            (
                receipt.detail
                for receipt in reversed(transport_parts)
                if receipt.status is not DeliveryStatus.VERIFIED and receipt.detail
            ),
            None,
        )
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.STICKER,
            transport=TransportReceipt(
                status,
                detail=sequence_issue or last_failure or "sticker_sequence",
            ),
            completion_mode=CompletionMode.BLOCKING,
            part_receipts=transport_parts,
            total_parts=len(parts),
        )

    async def execute_reply(
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
                receipt = await self.send_bubble(action, request_id=action_request_id)
            elif isinstance(action, ReactionRequest):
                receipt = await self.send_reaction(
                    action,
                    request_id=action_request_id,
                    capabilities=capabilities,
                )
            elif isinstance(action, StickerRequest):
                receipt = await self.send_sticker(action, request_id=action_request_id)
            elif isinstance(action, ChoicesRequest):
                receipt = await self.send_choices(action, request_id=action_request_id)
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

    async def send_seen_sticker(
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
            return self._missing_sticker_receipt(request_id, "sticker_catalog_unavailable")
        sticker = self._sticker_catalog.get(bot_namespace, file_unique_id)
        if sticker is None:
            return self._missing_sticker_receipt(
                request_id,
                "sticker_not_seen_in_bot_namespace",
            )
        return await self.send_sticker(
            StickerRequest(
                target=target,
                sticker_ref=sticker.file_id,
                text_before=text_before,
                text_after=text_after,
            ),
            request_id=request_id,
        )

    async def send_catalog_sticker(
        self,
        target: TargetRef,
        bot_namespace: str,
        catalog_id: str,
        *,
        request_id: str | None = None,
    ) -> InteractionReceipt:
        request_id = self._resolve_request_id(request_id)
        if self._sticker_catalog is None:
            return self._missing_sticker_receipt(request_id, "sticker_catalog_unavailable")
        sticker = self._sticker_catalog.resolve(bot_namespace, catalog_id)
        if sticker is None:
            return self._missing_sticker_receipt(request_id, "sticker_catalog_id_unknown")
        return await self.send_sticker(
            StickerRequest(target=target, sticker_ref=sticker.file_id),
            request_id=request_id,
        )

    def _missing_sticker_receipt(
        self,
        request_id: str,
        detail: str,
    ) -> InteractionReceipt:
        return self._receipt(
            request_id=request_id,
            kind=InteractionKind.STICKER,
            transport=TransportReceipt(DeliveryStatus.FAILED, detail=detail),
            completion_mode=CompletionMode.BLOCKING,
        )

    @staticmethod
    async def _call_single_async(
        call: Callable[[], Awaitable[object]],
    ) -> TransportReceipt:
        try:
            result = await call()
        except Exception:
            return TransportReceipt(DeliveryStatus.UNCERTAIN, detail="host_exception")
        if not isinstance(result, TransportReceipt):
            return TransportReceipt(DeliveryStatus.UNKNOWN, detail="invalid_transport_receipt")
        return result

    @staticmethod
    async def _call_sequence_async(
        call: Callable[[], Awaitable[object]],
        *,
        expected_count: int,
    ) -> tuple[tuple[TransportReceipt, ...], str | None]:
        try:
            result = await call()
        except Exception:
            return (
                (TransportReceipt(DeliveryStatus.UNCERTAIN, detail="host_exception"),),
                None,
            )
        return InteractionKernel._validate_sequence(result, expected_count=expected_count)
