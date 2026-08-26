# Hermes Telegram Interaction

A harness- and SDK-neutral Python core for Telegram interaction contracts,
ordered rich replies, and callback lifecycle state.

## Architecture

- The package owns public interaction contracts and callback state only.
- An injected host adapter owns Telegram transport, tokens, and client instances.
- The package uses only the Python standard library (`dependencies = []`).
- Python 3.11 or newer is required.

## Features

- Ordered bubble, reaction, regular-sticker, and choices actions with
  per-action receipts and stop-on-non-verified execution.
- In-memory and SQLite callback stores with TTL, user, chat, thread, and
  one-shot constraints.
- A bot-scoped SQLite catalog for regular stickers seen by each bot.
- Conservative paragraph, sentence, whitespace, and hard-limit text splitting.
- Explicit inbound-reaction capability reporting and bot-generated event
  filtering.

## Scope

Custom sticker creation and live host integrations are not included.

## License

MIT; see [LICENSE](LICENSE).
