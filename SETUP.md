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

Current release metadata:

    distribution: telemood
    import: telemood
    Python: >=3.11
    runtime dependencies: none

Finish with a short read-only report. Use `unknown` instead of guessing:

    TELEMOOD CAPABILITY REPORT
    Host runtime:            <framework and execution model>
    Transport ownership:     <owner of client, token, update loop>
    Boundary:                sync | async | mixed
    Send methods found:      <existing entrypoints>
    Sticker/callback routes: <paths | none>
    Reaction subscriptions:  change=<yes|no|unknown>, count=<yes|no|unknown>
    Usable now:              <bubble/reaction/sticker/choices subset>
    Degraded or missing:     <item — reason>
    Files to modify:         <list>
    Authorization needed:    <exact next action>

## 2. Authorization boundary

Safe before live authorization:

- read repository and host code, schemas, routes, permissions, and state-path
  shape without opening secret values;
- run synthetic unit tests and build a wheel from the checkout;
- run `check_adapter`, which never invokes transport;
- write the capability report.

Explicit human authorization is required before installation, host code or
configuration changes, live Telegram sends, restart/deployment, or any
push/tag/release. If an action is not clearly read-only or checkout-local,
treat it as authorization-required.

## 3. Install

After the owner authorizes changing the host Python environment, run from the
checkout:

    python -m pip install .
    python -c "import telemood; print(telemood.__version__)"

Expected version: `0.1.0`. Runtime dependencies are empty; normal Python
build requirements may still be used while building the package. Prefer a
normal install rather than adding the checkout to `sys.path`.

Built-in inbound normalization is limited to regular sticker messages,
reaction change/count updates, and callbacks containing a message object.
Generic text messages, Telegram Business messages, and inline-mode callbacks
must be routed and normalized by the host in this release.

## 4. Model plan

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
            callback_ttl_seconds=1800.0,
        ),
        sticker_catalog=sticker_catalog,
    )

Binding automatically expands long bubble text with a conservative heuristic: paragraph, sentence, whitespace, then hard split. Expanded bubbles stay in their original position relative to reactions, stickers, and choices.

Callback TTL is host-owned through `PlanContext` or
`action_plan_to_reply(..., callback_ttl_seconds=...)`; model JSON cannot set
it. Unknown versions, fields, action types, catalog IDs, and untrusted
identifiers fail closed. MiniApp is not part of the v0.1 public API.

## 5. Inject the existing transport

Use the included injected-client adapter, or implement `InteractionHost`
directly. The small facade below is complete and provider-neutral: each
host-owned callable accepts keyword arguments and returns `InjectedResult`.

    from telemood import InjectedResult, InjectedTelegramAdapter

    class ExistingClientFacade:
        def __init__(self, *, send_message, set_reaction, send_sticker, send_choices):
            self._send_message = send_message
            self._set_reaction = set_reaction
            self._send_sticker = send_sticker
            self._send_choices = send_choices

        def send_message(self, **kwargs):
            return self._send_message(**kwargs)

        def set_reaction(self, **kwargs):
            return self._set_reaction(**kwargs)

        def send_sticker(self, **kwargs):
            return self._send_sticker(**kwargs)

        def send_choices(self, **kwargs):
            return self._send_choices(**kwargs)

    facade = ExistingClientFacade(
        send_message=host_send_message,
        set_reaction=host_set_reaction,
        send_sticker=host_send_sticker,
        send_choices=host_send_choices,
    )
    adapter = InjectedTelegramAdapter(facade)

Each `host_*` callable maps the existing client operation to
`InjectedResult(accepted=..., provider_delivery_id=..., detail=...)`. An
explicit acceptance becomes `VERIFIED`; an explicit rejection becomes
`FAILED`; an invalid result becomes `UNKNOWN`; timeout or an exception with
an uncertain side effect becomes `UNCERTAIN`. Never claim acceptance merely
because a call returned.

For an async host, provide four equivalent `async def` facade methods and use
`AsyncInjectedTelegramAdapter` with `AsyncInteractionKernel`. Do not call
`asyncio.run()` or `loop.run_until_complete()` inside an already running event
loop, and do not create an event-loop bridge inside the adapter.

Check shape without calling transport:

    from telemood import check_adapter

    result = check_adapter(adapter, mode="sync")  # use mode="async" for async
    assert result.ok
    assert result.static_only
    assert not result.live_delivery_verified

Static conformance is not live Telegram verification.

For sticker sequences the adapter sends parts in order and stops on the first
non-`VERIFIED` result. A short receipt sequence is valid only when its final
receipt is non-`VERIFIED`; missing all-verified receipts, extra receipts, or
receipts after a non-verified result are protocol errors.

## 6. User-created regular sticker round trip

The included normalizer accepts a Telegram Bot API update mapping. The host
uses its existing Telegram client to obtain any visual media, then supplies the
bot namespace and optional logical media references. Telemood never downloads
media or receives the token.

    from telemood import (
        SQLiteStickerCatalog,
        ingest_incoming_sticker,
        list_sticker_model_views,
        normalize_incoming_sticker,
    )

    catalog = SQLiteStickerCatalog("state/stickers.sqlite3")
    event = normalize_incoming_sticker(
        update,
        bot_namespace=trusted_bot_namespace,
        thumbnail_ref=host_thumbnail_ref,
        media_ref=host_media_ref,
    )
    model_event = ingest_incoming_sticker(event, catalog)
    available_stickers = list_sticker_model_views(
        catalog,
        trusted_bot_namespace,
    )

`model_event.sticker` contains the opaque catalog ID, normalized text, and
optional logical media references. The event also reports sender kind, target
role, thread presence, and occurrence time without exposing the reusable
provider `file_id`. Without a media reference,
`model_event.sticker.text` explicitly says image content was not attached;
the model saw metadata, not the image.

Give the model `model_event` and, when it needs to choose from remembered
stickers, `available_stickers`. Never pass `catalog.list(...)` to the model;
those trusted storage rows contain reusable provider IDs. To send a sticker
again, the model places its safe `catalog_id` in the catalog sticker action
described in section 4. Plan binding resolves that ID only inside the trusted
`bot_namespace`; an unknown ID or an ID from another namespace fails closed.
The existing host adapter performs the actual send and returns the delivery
receipt.

Telemood does not create or modify sticker packs. A user creates and maintains
their regular pack with Telegram's in-app Sticker Editor or the `@Stickers`
Mini App, then sends a sticker to the host so it can be cataloged. See
[Telegram's sticker guide](https://core.telegram.org/stickers). Do not place
pack names, artwork, private moods, or provider `file_id` values in public
configuration or model plans.

v0.1 accepts regular stickers in static, animated, or video format. Mask and
`custom_emoji` sticker types are rejected and are not cataloged.

## 7. Incoming and outgoing reactions

Reaction sending is disabled until the host supplies confirmed capabilities:

    from telemood import (
        InteractionCapabilities,
        normalize_incoming_reaction_change,
        normalize_incoming_reaction_count,
    )

    capabilities = InteractionCapabilities(
        can_send_reactions=True,
        can_receive_reaction_changes=True,
        can_receive_reaction_counts=True,
        message_reaction_subscribed=True,
        message_reaction_count_subscribed=True,
        available_reactions=("👍", "👀"),
        reaction_change_unavailable_reason=None,
        reaction_count_unavailable_reason=None,
    )

Normalize `message_reaction` and `message_reaction_count` updates separately:

    change = normalize_incoming_reaction_change(update)
    count = normalize_incoming_reaction_count(update)

Change updates preserve actor and old/new reaction sets; count updates contain
anonymous aggregates and may be delayed. Their subscription flags and
unavailable reasons are independent. `ReactionValue` can represent emoji,
custom emoji, and paid reactions; v0.1 execution accepts ordinary emoji only.
`InteractionKernel.accept_incoming_reaction` returns an explicit acceptance or
rejection reason and optional capability detail.

Telegram reaction updates must be explicitly requested by the host and may require administrator permission. Bot-originated sends must not be synthesized as inbound updates.

## 8. Callbacks and execution

Use a host-owned durable callback store when callbacks must survive restarts.
Choose the kernel matching the adapter:

    from telemood import (
        AsyncInteractionKernel,
        InteractionKernel,
        SQLiteCallbackStore,
        normalize_callback_query,
    )

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

    async_kernel = AsyncInteractionKernel(
        async_adapter,
        callbacks=SQLiteCallbackStore("state/callbacks.sqlite3"),
        sticker_catalog=catalog,
    )
    async_receipt = await async_kernel.execute_reply(
        reply,
        request_id=trusted_request_id,
        capabilities=capabilities,
    )

Actions run strictly in plan order. The kernel waits for each receipt and stops on FAILED, UNKNOWN, or UNCERTAIN. The plan receipt records action receipts, stop index, and unexecuted count. Sticker multi-part compatibility requests preserve every returned TransportReceipt.

Choices bind callback handles to user/chat/thread, host-owned TTL,
pending/active state, and one-shot consumption. Handles are limited to
Telegram's 64 UTF-8 byte `callback_data` boundary. Normalize and consume a
callback through the same kernel/store:

    callback = normalize_callback_query(update)
    resolution = kernel.consume_callback(
        callback.token,
        user_id=callback.user_id,
        chat_id=callback.target.chat_id,
        thread_id=callback.target.thread_id,
    )

A successful choices `InteractionReceipt` exposes `callback_expires_at`, the
earliest absolute Unix expiry supplied by its active callback handles. The host
can combine that deadline with `provider_delivery_id` and its existing
scheduler/Telegram client to remove or disable the stale reply markup. Telemood
does not start a scheduler, thread, event loop, or second client.

Expiry enforcement remains in the callback store: a stale press still fails
closed even when visual cleanup has not run or fails. Custom `CallbackStore`
implementations remain compatible when they return `CallbackToken(value)`;
their receipt expiry is `None` unless they also populate the token's optional
`expires_at` metadata.

## 9. Minimum offline verification

Run these from the checkout before installation and repeat them after changes:

    python -m unittest discover -s tests -v
    python -m pip wheel . --no-deps -w dist

Use synthetic data only. A real send, host mutation, restart, deployment, push, tag, or release requires separate human authorization.
