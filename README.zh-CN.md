# Telemood

[English](README.md) | [配置指南](SETUP.zh-CN.md)

Telemood 适用于已经有 Telegram 运行时（Telegram Bot API 连接完备）且可复用既有客户端的模型驱动 Agent。

你可以从内置的免依赖 adapter 门面开始，或实现宿主协议，将既有 client 方法映射到 Telemood 的结构化交互内核。

## 在 Telegram 里的实际效果

一次回答可以严格按顺序组合：对触发消息的 reaction、几条短气泡、普通贴纸和可点按的选择按钮：

> **你：** 帮我看看服务为什么停了
>
> **Agent：** 👀 *（对你的消息作出 reaction）*
>
> 我正在检查最近的事件。 *（气泡）*
>
> 连接失败了，下一步怎么处理？ *（气泡）*
>
> `[重试]` `[查看详情]` *（选择按钮）*

## 速答

**Telemood 是什么？**
一个零第三方运行时依赖的 Python 库，把模型计划变成 Telegram 气泡、emoji reaction、普通贴纸和选择按钮，并为每个动作保留明确回执；同步和异步宿主都支持。

**它是另一个 bot 或 client 吗？**
不是。Telemood 复用宿主已有的 client 与 update loop，不读取 bot token，也不启动第二个进程。

**能直接把仓库交给 coding Agent 吗？**
可以。Agent 应先只读检查、报告能力缺口，并在安装或修改宿主前等待授权。

**怎么安装？**
授权后，在 Python `>=3.11` 的仓库检出目录执行 `python -m pip install .`。

**异步宿主怎么办？**
使用 `AsyncInteractionKernel`、`AsyncInjectedTelegramAdapter` 与 `check_adapter(..., mode="async")`；不要在 adapter 内部另造 event-loop bridge。

## 给 Agent 的提示词

将以下提示词发送给你的 AI Agent：

```text
请先克隆（或拉取）https://github.com/beniedev/telemood（若本机尚无检出）。
请先只读检查仓库与宿主运行时，报告 transport 边界与能力缺口。
宿主仍是 Telegram 客户端与 bot token 的唯一持有者。
请勿要求读取/索取 token，也不要创建第二套 Telegram 客户端或 update loop。
请确认 transport 边界是同步还是异步，再选用同模式的 Telemood kernel、adapter 与 check_adapter。
不要在 adapter 内部桥接正在运行的 event loop。
先在仓库检出内运行离线单元测试与 wheel 构建；未经明确授权，不要安装或修改宿主。
获得授权后，按 SETUP.zh-CN.md 安装 Telemood，并把既有 client 映射到 injected-client facade 或宿主协议。
此后才可改动宿主代码/配置、发送、重启或部署；模型行为必须保持在已报告的能力边界内。
```

## Telemood 的边界

- 轻量级交互内核：面向 Telegram 的结构化动作与回执
- Python 版本：`>=3.11`
- `dependencies = []`（表示零运行时依赖，不意味着无需常规 Python 环境与构建工具）
- 发行包与 Python 导入名统一为 `telemood`
- Core 与具体 harness、SDK 解耦
- 本版本不提供自制贴纸创建/发布能力
- v0.1 公开 API 不包含 MiniApp

## Telemood 的能力

- 有序动作：文本气泡、表情回应、贴纸、选择按钮
- 任一动作未达 `VERIFIED` 时停止后续执行，并校验 sticker 的合法提前停止
- 版本化模型计划：`telemood.plan.v1`，动作鉴别字段为 `type`；callback TTL 由宿主持有
- 受控的回调生命周期与回调状态存储
- 按 Bot 作用域持久化普通贴纸目录，并提供安全的 `StickerModelEvent` 模型投影
- plan 绑定时自动执行保守的语义气泡启发式切割
- reaction change 与匿名 reaction count 分别报告订阅状态和不可用原因
- 同时提供同步 `InteractionKernel` 与异步 `AsyncInteractionKernel`
- 内置同步/异步 injected-client adapter 与 Bot API mapping normalizer

仓库名、发行包名与 Python 导入名均统一为 Telemood。

## 状态与验证

Telemood `0.1.0` 是早期预发布。contracts 与 adapters 已由合成离线测试覆盖，但尚未对所有 Telegram SDK 或宿主运行时完成验证，API 仍可能变化。

CI 会在 Python 3.11、3.12、3.13 上运行完整单元测试，构建 wheel，检查路径白名单与选定的敏感内容模式，并验证干净安装和 `import telemood`。这些检查验证的是包本身，不代表任意具体生产部署已经安全。

## 速览配置

[SETUP.md](SETUP.md) 是权威接入指南；[SETUP.zh-CN.md](SETUP.zh-CN.md) 是其中文翻译。支持便携式 skill 的 Agent 可以另外阅读 [`skills/telemood/SKILL.md`](skills/telemood/SKILL.md)。有疑问时，以接入指南和仓库代码为准；授权协议见 [LICENSE](LICENSE)。
