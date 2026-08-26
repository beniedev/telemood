---
name: telemood
description: Integrate an existing Telegram host transport into Telemood when a trusted host runtime already owns token/client state.
---

# Telemood Skill (Optional)

Use this only when you are wiring Telemood into an existing Telegram host agent and the host already owns transport, token, and runtime lifecycle.

1. Read both `../../SETUP.md` and `../../SETUP.zh-CN.md` first.
2. Reuse the host’s existing Telegram client and SDK, and never introduce a second token/client.
3. Never ask for, read, copy, or persist Telegram bot tokens.
4. Inspect host permissions and transport methods before adding any adapter code.
5. Run static inspection (`check_adapter`) and offline synthetic verification before any host call.
6. Ask for explicit human authorization before:
   - changing host code or config
   - sending live Telegram messages
   - restarting or deploying host services
7. If any feature is unavailable, report capability degradation with explicit reason and no silent fallback.
