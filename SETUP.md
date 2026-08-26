# Telemood setup guide

[简体中文](SETUP.zh-CN.md)

This guide is for an agent integrating Telemood into an existing Telegram bot host. The host remains the sole owner of its token, client, polling/webhook loop, thread routing, and reconnect lifecycle.

## 1. Before changing the host

Inspect the host read-only and confirm:

- existing send methods and their provider results;
- incoming message, sticker, reaction, and callback routes;
- reaction update subscriptions and required bot permissions;
- host-owned paths for callback and sticker SQLite state.

Do not read or copy the bot token. Do not create a second client or update loop.

Current pre-release metadata:

    distribution: telemood
    import: telemood
    Python: >=3.11
    runtime dependencies: none

## 2. Model plan

The model returns JSON-compatible data only. It cannot choose a chat, user, thread, bot namespace, Telegram file_id, token, endpoint, or SDK object.

    {
      "version": "telemood.plan.v1",
      "actions": [
        {"type": "bubble", "text": "I am checking that now."},
        {"type": "reaction", "target": "trigger_message", "emoji": "👀"},
        {
          "type": "sticker",
          "sticker": {"kind": "catalog", "id": "sticker_opaque_logical_id"}
        },
        {
          "type": "choices",
          "prompt": "Continue?",
          "options": [
            {"key": "yes", "label": "Yes"},
            {"key": "no", "label": "No"}
          ]
        }
      ]
    }

Parse first, then bind trusted host context:

    from telemood import (
        PlanContext,
        bind_interaction_plan,
        parse_interaction_plan,
    )

    typed_plan = parse_interaction_plan(model_output)
    reply = bind_interaction_plan(
        typed_plan,
        PlanContext(
            target=trusted_target,
            authorized_user_id=trusted_user_id,
            bot_namespace=trusted_bot_namespace,
        ),
        sticker_catalog=sticker_catalog,
    )

Binding automatically expands long bubble text with a conservative heuristic: paragraph, sentence, whitespace, then hard split. Expanded bubbles stay in their original position relative to reactions, stickers, and choices.

Unknown versions, fields, action types, catalog IDs, and untrusted identifiers fail closed.

## 3. Inject the existing transport

Implement the four synchronous InteractionHost methods around the client the host already owns:

    from telemood import DeliveryStatus, TransportReceipt

    class ExistingClientAdapter:
        def __init__(self, existing_client):
            self.client = existing_client

        def send_bubble(self, request_id, request):
            result = self.client.send_message(request.target, request.text)
            return TransportReceipt(
                DeliveryStatus.VERIFIED,
                provider_delivery_id=str(result.message_id),
            )

        def send_reaction(self, request_id, request):
            ...

        def send_sticker_sequence(self, request_id, request, parts):
            # Return one TransportReceipt per attempted part, in order.
            ...

        def send_choices(self, request_id, request, callback_tokens):
            ...

This example is intentionally SDK-neutral. Do not return VERIFIED unless the provider explicitly confirmed the effect. Map explicit rejection to FAILED, an invalid result to UNKNOWN, and a timeout or uncertain side effect to UNCERTAIN.

Check shape without calling transport:

    from telemood import check_adapter

    result = check_adapter(adapter)
    assert result.ok
    assert result.static_only
    assert not result.live_delivery_verified

Static conformance is not live Telegram verification.

## 4. Incoming regular stickers

The host normalizes an incoming Telegram sticker and may attach logical media references. Core never downloads media and never receives the token.

    from telemood import (
        IncomingSticker,
        IncomingStickerEvent,
        SQLiteStickerCatalog,
        StickerFormat,
        StickerType,
        ingest_incoming_sticker,
    )

    catalog = SQLiteStickerCatalog("state/stickers.sqlite3")
    event = IncomingStickerEvent(
        target=trusted_target,
        sender_user_id=trusted_sender_id,
        received_at=provider_timestamp,
        sticker=IncomingSticker(
            bot_namespace=trusted_bot_namespace,
            file_id=provider_sticker.file_id,
            file_unique_id=provider_sticker.file_unique_id,
            type=StickerType.REGULAR,
            format=StickerFormat.ANIMATED,
            emoji=provider_sticker.emoji,
            set_name=provider_sticker.set_name,
            thumbnail_ref=host_thumbnail_ref,
            media_ref=host_media_ref,
        ),
    )
    model_view = ingest_incoming_sticker(event, catalog)

model_view contains the opaque catalog ID, normalized text, and optional logical media references. It never contains the reusable provider file_id. Without a media reference, its text explicitly says that image content was not attached.

v0.1 accepts regular stickers in static, animated, or video format. mask and custom_emoji sticker types are rejected and are not cataloged.

## 5. Incoming and outgoing reactions

Reaction sending is disabled until the host supplies confirmed capabilities:

    from telemood import InteractionCapabilities

    capabilities = InteractionCapabilities(
        can_send_reactions=True,
        can_receive_reaction_changes=True,
        can_receive_reaction_counts=True,
        reaction_updates_subscribed=True,
        available_reactions=("👍", "👀"),
    )

Use IncomingReactionChange for actor-bound old/new reaction sets. Use IncomingReactionCount for anonymous aggregate counts. ReactionValue represents emoji, custom_emoji, and paid; v0.1 accepts only ordinary emoji for execution. InteractionKernel.accept_incoming_reaction returns ReactionAcceptance with an explicit rejection reason instead of silently returning None.

Telegram reaction updates must be explicitly requested by the host and may require administrator permission. Bot-originated sends must not be synthesized as inbound updates.

## 6. Callbacks and execution

Use a host-owned durable callback store when callbacks must survive restarts:

    from telemood import InteractionKernel, SQLiteCallbackStore

    kernel = InteractionKernel(
        adapter,
        callbacks=SQLiteCallbackStore("state/callbacks.sqlite3"),
        sticker_catalog=catalog,
    )
    receipt = kernel.execute_reply(
        reply,
        request_id=trusted_request_id,
        capabilities=capabilities,
    )

Actions run strictly in plan order. The kernel waits for each receipt and stops on FAILED, UNKNOWN, or UNCERTAIN. The plan receipt records action receipts, stop index, and unexecuted count. Sticker multi-part compatibility requests preserve every returned TransportReceipt.

Choices bind callback handles to user/chat/thread, TTL, pending/active state, and one-shot consumption. Handles are limited to Telegram's 64 UTF-8 byte callback_data boundary.

## 7. Minimum offline verification

    python -m unittest discover -s tests -v
    python -m pip wheel . --no-deps -w dist

Use synthetic data only. A real send, host mutation, restart, deployment, push, tag, or release requires separate human authorization.
