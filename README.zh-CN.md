# Telemood

[English](README.md) | [配置指南](SETUP.zh-CN.md)

Telemood 适用于已经有 Telegram 运行时（Telegram Bot API 连接完备）且可复用既有客户端的模型驱动 Agent。

你不需要手工编写接入胶水代码。将仓库交给你的 AI Agent，由其读取现有运行时并接入 Telemood 的结构化交互内核。

## 给 Agent 的提示词

将以下提示词发送给你的 AI Agent：

```text
请先克隆（或拉取）https://github.com/beniedev/telemood（若本机尚无检出）。
请先只读检查：先阅读仓库和宿主运行时边界，确认传输链路与权限后再给出建议。
宿主仍是 Telegram 客户端与 bot token 的唯一持有者。
请勿要求读取/索取 token，也不要创建第二套 Telegram 客户端。
仅在明确人工授权后，才将 Telemood 接入既有运行时，并在授权后改动宿主代码/配置、触发发送、重启或部署。
请先输出能力缺口报告，并按该能力边界约束模型输出。
```

## Telemood 的边界

- 轻量级交互内核：面向 Telegram 的结构化动作与回执
- Python 版本：`>=3.11`
- `dependencies = []`（表示零运行时依赖，不意味着无需常规 Python 环境与构建工具）
- 发行与导入兼容名：`hermes-telegram-interaction` / `hermes_telegram_interaction`
- 不依赖 Hermes
- 本版本不提供自制贴纸创建/发布能力

## Telemood 的能力

- 有序动作：文本气泡、表情回应、贴纸、选择按钮
- 任一动作未达 `VERIFIED` 时可停止后续执行
- 使用 `kind` 作为动作鉴别符（非 `type`）
- 受控的回调生命周期与回调状态存储
- 按 Bot 作用域持久化普通贴纸目录（`SQLiteStickerCatalog`）
- 显式调用 `split_semantic_bubbles`（非自动语义切分）

## 速览配置

请优先阅读 [SETUP.zh-CN.md](SETUP.zh-CN.md) 作为落地指南，以及 [LICENSE](LICENSE) 了解授权。

可选：若你的 Agent 支持便携式 `SKILL.md` 约定，可先阅读 [`skills/telemood/SKILL.md`](skills/telemood/SKILL.md)；不支持该约定的 Agent 请直接阅读 [SETUP.zh-CN.md](SETUP.zh-CN.md)。`SETUP.zh-CN.md` 与仓库代码仍为权威说明。
