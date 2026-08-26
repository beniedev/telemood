# Telemood

[简体中文](README.zh-CN.md) | [Setup Guide](SETUP.md)

Telemood is for owners already running a **model-driven Telegram Agent** with an existing Telegram Bot API runtime and client.

You do not need to hand-code a transport adapter. Give this repository to your Agent and let it wire the existing runtime to Telemood’s structured interaction kernel.

## Prompt for your Agent

Copy and paste this prompt to your AI Agent:

```text
Clone (or pull) https://github.com/beniedev/telemood if needed.
Inspect the repository and host runtime in read-only mode first, then report transport boundaries and permission gaps.
Keep this host as the sole owner of its Telegram client and bot token.
Do not ask for or read any bot token, and do not create a second Telegram client.
After explicit human authorization, integrate Telemood into the existing runtime and only then mutate host code/config, send, restart, or deploy.
Run a capability-gap report before any production send and keep model behavior inside those capabilities.
```

## What Telemood Is

- Lightweight Telegram interaction kernel for model-driven action plans
- Python `>=3.11`
- `dependencies = []` means no third-party runtime dependencies, while normal Python/build tooling is still required.
- Distribution and import: `telemood`
- Harness- and SDK-neutral core

## What Telemood Is Not

- Not a standalone bot process, poller, webhook server, or scheduler
- Not a token manager or network credential store
- Not an automatic deployer, restart tool, or live monitor
- Not a Telegram media downloader
- Not a custom sticker creator or sticker-set publisher in this release

## Core Capabilities

- Ordered actions: bubble, reaction, sticker, choices
- Per-action receipts and stop-on-non-verified execution
- Versioned model plan (`telemood.plan.v1`, `type` discriminator) bound to host-owned context
- One-shot callbacks with bounded store lifetime
- Bot-scoped `SQLiteStickerCatalog` with opaque model-visible IDs for seen regular stickers
- Automatic conservative bubble splitting during plan binding
- Explicit reaction-change and anonymous reaction-count contracts with conservative capabilities

The repository, distribution, and Python import all use the public name Telemood.

## Setup

See [SETUP.md](SETUP.md) for the authoritative implementation guide, and [LICENSE](LICENSE) for this project license.

Optional: If your agent supports the portable `SKILL.md` convention, you can also read [`skills/telemood/SKILL.md`](skills/telemood/SKILL.md). Agents without SKILL support should use [SETUP.md](SETUP.md) directly. `SETUP.md` and the repository code remain authoritative.
