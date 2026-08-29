---
name: telemood
description: Integrate an existing Telegram host transport into Telemood when a trusted host runtime already owns token/client state.
---

# Telemood Skill (Optional)

Use this only when you are wiring Telemood into an existing Telegram host agent and the host already owns transport, token, and runtime lifecycle.

1. Follow `../../SETUP.md`; `../../SETUP.zh-CN.md` is its translation. Read one, not both.
2. Reuse the host’s existing Telegram client and update loop. Never ask for, read, copy, or persist bot tokens, and never introduce a second client.
3. Inspect permissions, routes, state ownership, and whether the transport boundary is sync, async, or mixed before writing adapter code.
4. Use the matching native path: `InteractionKernel` / `InjectedTelegramAdapter` for sync, or `AsyncInteractionKernel` / `AsyncInjectedTelegramAdapter` for async. Do not bridge a running event loop inside the adapter.
5. For user-created regular sticker round trips, let the host resolve visual media and pass only logical `thumbnail_ref` / `media_ref` values to Telemood. Give the model `StickerModelEvent` or `list_sticker_model_views(...)`, never `StickerCatalog.list(...)` or Telegram `file_id`; resolve the safe catalog ID only inside the trusted bot namespace when sending it back. Reject mask stickers and `custom_emoji`.
6. Sticker packs remain user-managed through Telegram's UI. Do not add pack creation, upload, update, private artwork, pack names, moods, or provider IDs to the public integration.
7. Produce the capability report defined in SETUP, then run `check_adapter` in the matching mode and the synthetic offline tests.
8. Ask for explicit human authorization before:
   - installing into or otherwise changing the host environment
   - changing host code or config
   - sending live Telegram messages
   - restarting or deploying host services
7. If any feature is unavailable, report capability degradation with explicit reason and no silent fallback.
