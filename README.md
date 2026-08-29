# Telemood

[简体中文](README.zh-CN.md) | [Setup Guide](SETUP.md)

**Telemood adds expressive, structured replies to an existing model-driven Telegram bot.**

A model can combine text bubbles, emoji reactions, regular stickers, and tappable choices in one reply. Telemood sends those actions in order through the Telegram client you already run and records which actions actually succeeded.

Telemood is not another bot framework or Telegram client. It does not own your bot token, update loop, or deployment.

## What a reply looks like

> reaction → text bubbles → sticker → choices

<img src="assets/telemood-telegram-demo.png" alt="Telegram demo showing text bubbles, a reaction, a regular sticker, and choice buttons in one ordered reply" width="640">

Telemood preserves the planned order and records the result of each action.

## Personal sticker round trip

Need a pack first? [Create a regular sticker pack from your own images](STICKER_PACK_GUIDE.md), then send one of its stickers to your bot.

When a user sends a sticker from a regular sticker pack, the host can attach a
thumbnail or media reference for its vision-capable model. Telemood remembers
the sticker under an opaque, bot-scoped catalog ID. The model can later choose
that ID in a reply plan, and the host's existing Telegram client sends the
original sticker back.

Telegram `file_id` values stay inside the trusted catalog and transport
boundary. Without visual media, Telemood explicitly reports that the model saw
metadata only. Mask stickers and Telegram `custom_emoji` are not part of this
release.

## The core idea

```text
Model → reply plan → Telemood → existing Telegram client → execution results
```

The existing bot or runtime (the **host**) still decides where and through which client the reply is sent. Telemood parses the model's plan, binds trusted host context, and executes it. The model cannot choose chat, user, thread, bot namespace, credentials, or Telegram `file_id` values.

## Quick answers

**What is Telemood?** A small Python library that turns model-generated plans into richer Telegram replies.

**Is this another bot or client?** No. It reuses the Telegram client and update loop you already have.

**What can it do?** Ordered text bubbles, ordinary emoji reactions, cataloged regular stickers, and one-shot choice buttons.

**How do I integrate it?** Choose the sync or async path, adapt your existing client, and bind trusted host context. The exact contract is in [SETUP.md](SETUP.md).

## Scope and capabilities

### Build richer replies

- Text bubbles, with conservative splitting for long text
- Ordinary emoji reactions on the triggering message
- Understand and remember regular stickers sent by users, then send them back by safe catalog ID
- Choice buttons within Telegram's 64-byte `callback_data` limit

### Execute safely

- Actions run in order; any result other than `VERIFIED` stops later actions.
- A returned function call is not treated as proof of Telegram delivery.
- Plans use `telemood.plan.v1`; unknown or untrusted input fails closed.
- Trusted identifiers and button lifetime are supplied by the host after parsing.

### Fit existing hosts

- Native synchronous and asynchronous execution
- One-shot callbacks with host-owned lifetime
- Safe logical sticker IDs instead of model-visible Telegram `file_id` values
- Normalizers for regular stickers, reaction changes/counts, and message-backed callbacks

Telemood does **not** run a second bot or update loop, read or store your token, download Telegram media, manage deployment, create sticker packs, or provide a MiniApp framework in v0.1.

Python `>=3.11` · zero third-party runtime dependencies · package/import name `telemood`

## Prompt for your Agent

```text
Clone or pull https://github.com/beniedev/telemood if needed.
Inspect the repository and host runtime read-only first; report the existing Telegram send path and supported Telemood features.
Keep the host as the sole owner of its client, token, update loop, and trusted routing identifiers. Do not create a second client or loop.
Choose the matching sync or async Telemood path. Run offline tests and build a wheel.
Do not install, modify the host, send, restart, or deploy before explicit human authorization.
After authorization, follow SETUP.md and adapt the existing client.
```

## Status and verification

Telemood `0.1.0` is the first public release. Its core contracts and adapters have offline test coverage, but it has not been tested against every Telegram SDK or production host. APIs may still change within the `0.x` series.

Built-in inbound normalization covers regular sticker messages, reaction change/count updates, and message-backed callbacks. Generic text messages, Telegram Business messages, and inline-mode callbacks remain host-routed. Reaction updates require explicit subscription and may require administrator permission.

CI tests Python 3.11–3.13, builds the wheel, checks package contents, and verifies a clean install. CI verifies the package, not a particular production deployment. Static adapter checks are not live Telegram verification.

[SETUP.md](SETUP.md) is authoritative for integration. Also available: [Chinese setup](SETUP.zh-CN.md), [portable Agent skill](skills/telemood/SKILL.md), and [license](LICENSE).
