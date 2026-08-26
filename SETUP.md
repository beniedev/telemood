# Telemood Integration & Setup Guide

This document is authoritative for humans and coding Agents integrating
`hermes_telegram_interaction` into an existing Telegram host agent.

Repository: `https://github.com/beniedev/telemood`

## 0) Authorization Boundary (Required)

- Do read-only inspection, static checks, and offline synthetic tests first.
- The host remains the sole owner of its configured bot token. Telemood/adapter integration must only reuse the existing host Telegram transport/client and must not request, read, copy, or persist the token or any raw SDK handle.
- Host code/config mutation, live Telegram calls, process restarts, and deploy/restart actions require explicit human authorization.
- Before any live run, produce and share an explicit capability-gap report.

## 1) Preconditions

- Python requirement: `>=3.11`
- Distribution/package: `hermes-telegram-interaction` (`import hermes_telegram_interaction`)
- Runtime dependencies: `dependencies = []` means no runtime package dependencies; it does not remove normal Python build/tooling needs.
- No Hermes dependency and no direct transport ownership by this package.
- No custom sticker creation or custom sticker-set publishing in this release.

Minimal install step in a cloned checkout (after authorization):

```bash
python -m pip install .
```

## 2) Host Inspection (Read-only)

Before wiring:

1. Confirm host owns Telegram client, token, SDK, and transport lifecycle.
2. Locate inbound message/callback/reaction/sticker paths.
3. Confirm a synchronous adapter boundary from host logic.
4. Choose host-owned state paths (for example `state/callbacks.sqlite3` and `state/stickers.sqlite3`).

## 3) Action Source Trust Boundary

`action_plan_to_reply` accepts either:

- `{"actions": [...]}` or
- `[...]`

Only trusted host code injects:

- `target`
- `authorized_user_id`
- any target endpoint/client fields

The model output must include only supported action content.

```python
from hermes_telegram_interaction import (
    TargetRef,
    action_plan_to_reply,
)

trusted_target = TargetRef(
    channel="telegram",
    chat_id="chat-id",
    message_id="message-id",
    thread_id="thread-id",
)

plan = {
    "actions": [
        {"kind": "bubble", "text": "Analyzing the request"},
        {"kind": "reaction", "emoji": "👀"},
        {"kind": "sticker", "sticker_ref": "known-regular-sticker-ref"},
        {"kind": "bubble", "text": "I will proceed next"},
        {"kind": "choices", "prompt": "Continue?", "options": [{"key": "yes", "label": "Yes"}, {"key": "no", "label": "No"}], "callback_ttl_seconds": 1200},
    ]
}

reply = action_plan_to_reply(
    plan,
    target=trusted_target,
    authorized_user_id="trusted-user-id",
)
```

`kind` is the only action discriminator (`bubble`, `reaction`, `sticker`, `choices`).
The model must **not** inject target context, token/endpoint, auth user, or tokenized callback data.

Sticker actions are `{"kind": "sticker", "sticker_ref": "<logical-sticker-ref>"}`.
`sticker_ref` must be a known logical/file reference supplied or already allowed by the host or catalog.
It is not an endpoint; the model cannot invent `target`, `user`, `token`, or `endpoint` fields.
`action_plan_to_reply` does not validate catalog membership.

## 4) Exact Host Adapter Protocol (Synchronous)

The host adapter must expose these exact sync methods:

```python
from typing import Mapping, Sequence
from hermes_telegram_interaction import (
    BubbleRequest,
    CallbackToken,
    ChoicesRequest,
    InteractionHost,
    ReactionRequest,
    StickerRequest,
    StickerPart,
    TransportReceipt,
)

class TelegramHostAdapter(InteractionHost):
    def send_bubble(self, request_id: str, request: BubbleRequest) -> TransportReceipt: ...

    def send_reaction(
        self,
        request_id: str,
        request: ReactionRequest,
    ) -> TransportReceipt: ...

    def send_choices(
        self,
        request_id: str,
        request: ChoicesRequest,
        callback_tokens: Mapping[str, CallbackToken],
    ) -> TransportReceipt: ...

    def send_sticker_sequence(
        self,
        request_id: str,
        request: StickerRequest,
        parts: Sequence[StickerPart],
    ) -> Sequence[TransportReceipt]: ...
```

`InteractionHost` above only defines request boundaries; you may keep additional host methods.

`DeliveryStatus` currently maps as:

- `VERIFIED`: confirmed success
- `FAILED`: confirmed transport failure
- `UNKNOWN`: uncertain confirmation
- `UNCERTAIN`: partial/unverified completion

`check_adapter` is static-only validation and does not call host transport.

```python
from hermes_telegram_interaction import check_adapter

result = check_adapter(adapter)
```

- `result.ok` is the main public success flag (`result.passed` is equivalent).
- `result.static_only` must be `True` for the current static check mode.
- `result.live_delivery_verified` is always `False` for `check_adapter` because it is static-only; a separately authorized live probe produces separate evidence and does not mutate this result.
- `result.issues` is a tuple of per-method reasons when checks fail.

## 5) Kernel and State Initialization

```python
from hermes_telegram_interaction import (
    InteractionKernel,
    SQLiteCallbackStore,
    SQLiteStickerCatalog,
)

callback_store = SQLiteCallbackStore("state/callbacks.sqlite3")
sticker_catalog = SQLiteStickerCatalog("state/stickers.sqlite3")

kernel = InteractionKernel(
    host=adapter,
    callbacks=callback_store,
    sticker_catalog=sticker_catalog,
)
```

`sticker_catalog` is caller-owned state and may be passed by host path policy.

## 6) Regular Sticker Receive/Send Flow (Correct)

Telemood does not download media and does not create custom sticker sets.
It works with regular sticker references only.

### 6.1 Inbound sticker normalization and catalog

```python
from hermes_telegram_interaction import IncomingSticker

# pseudocode
sticker_catalog = SQLiteStickerCatalog("state/stickers.sqlite3")
sticker = IncomingSticker(
    bot_namespace="bot-namespace",
    file_id=raw_sticker.file_id,
    file_unique_id=raw_sticker.file_unique_id,
    emoji=raw_sticker.emoji,
    set_name=raw_sticker.set_name,
)
sticker_catalog.remember(sticker)
```

### 6.2 Outbound sticker action from model

```python
from hermes_telegram_interaction import (
    TargetRef,
    action_plan_to_reply,
)

reply = action_plan_to_reply(
    {"actions": [{"kind": "sticker", "sticker_ref": "known-file-id"}]},
    target=TargetRef(channel="telegram", chat_id="chat-id", message_id="message-id"),
    authorized_user_id="trusted-user-id",
)
```

The host can feed `known-file-id` from a catalog-derived logical map it already exposes.

### 6.3 Outbound regular sticker replay by incoming identity

```python
kernel.send_seen_sticker(
    target=TargetRef(channel="telegram", chat_id="chat-id", message_id="message-id"),
    bot_namespace="bot-namespace",
    file_unique_id="file-unique-id",
)
```

`send_seen_sticker` uses `bot_namespace` + `file_unique_id` to map the known sticker reference in `sticker_catalog`.

## 7) Text Splitting Is Explicit

`split_semantic_bubbles` is **not automatic**.

```python
from hermes_telegram_interaction import split_semantic_bubbles

bubbles = split_semantic_bubbles(long_text, max_length=4096)
```

## 8) Execute and Capability Reporting

```python
from hermes_telegram_interaction import (
    InteractionCapabilities,
)

reply = action_plan_to_reply(plan, target=trusted_target, authorized_user_id="trusted-user-id")
receipts = kernel.execute_reply(
    reply,
    request_id="request-001",
    capabilities=InteractionCapabilities(
        can_send_reactions=True,
        can_receive_reactions=True,
        available_reactions=("👍", "👀"),
    ),
)
```

`InteractionCapabilities` fields are:

- `can_send_reactions`
- `can_receive_reactions`
- `available_reactions`

## 9) Inbound Routing

### 9.1 Inline callback

```python
from hermes_telegram_interaction import CallbackToken

# pseudocode
resolution = kernel.consume_callback(
    CallbackToken(raw_callback_data),
    user_id="trusted-user-id",
    chat_id="chat-id",
    thread_id="thread-id",  # optional
)
if resolution.accepted:
    action_key = resolution.payload.value
else:
    failure = resolution.reason
```

### 9.2 Sticker inbound (normalization example)

See Section 6.1 for the normal sticker catalog intake flow.

### 9.3 Reaction inbound

```python
from hermes_telegram_interaction import (
    InteractionCapabilities,
    InteractionKernel,
    IncomingReaction,
    TargetRef,
)

# pseudocode
reaction = IncomingReaction(
    target=TargetRef(channel="telegram", chat_id="chat-id", message_id="message-id", thread_id="thread-id"),
    emoji="👍",
    user_id="user-id",
    bot_generated=False,
)
normalized = InteractionKernel.accept_incoming_reaction(
    reaction,
    capabilities=InteractionCapabilities(can_receive_reactions=True),
)
```

`incoming reaction` should include `target` + `emoji` + `user_id`.

## 10) Callback Resolution Shape

`CallbackResolution` fields are:

- `accepted`
- `reason` (from `CallbackRejection`)
- `payload` (`CallbackPayload`)

No other fields are assumed.

## 11) Three-Tier Verification

### Tier 1: Static check

```python
from hermes_telegram_interaction import check_adapter

result = check_adapter(adapter)
assert result.ok
assert result.issues == ()
assert result.static_only
assert not result.live_delivery_verified
```

```bash
python -m unittest tests.test_tm02_agent_setup -v
```

### Tier 2: Full local regression

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

### Tier 3: Authorized live probe

Only perform one short authorized live probe after explicit host authorization.
Static and offline tests are not proof of live Telegram delivery.

## 12) Capability Report Template

```markdown
### Telemood Adapter Capability Report
- Host transport: <SDK + version>
- Python: <version>
- send_bubble: Supported / Unsupported
- send_reaction: Supported / Unsupported
- send_choices: Supported / Unsupported
- send_sticker_sequence: Supported / Unsupported
- can_send_reactions: true/false
- can_receive_reactions: true/false
- available_reactions: ["👍", ...]
- state files: `state/callbacks.sqlite3`, `state/stickers.sqlite3`
- check_adapter passed: true/false
- check_adapter static_only: true
- check_adapter live_delivery_verified: false
- issues: []
```

Report capability degradation honestly; do not mark failed items as verified.

## 13) Rollback and State Preservation

1. Disable hooks or route to old host path before process changes.
2. After explicit authorization, restart host only if required.
3. Preserve `state/callbacks.sqlite3` and `state/stickers.sqlite3`; never delete during rollback.
