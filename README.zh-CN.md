# Telemood

[English](README.md) | [配置指南](SETUP.zh-CN.md)

**Telemood 给已有的模型驱动 Telegram Bot 加上一层更丰富、更有结构的回复方式。**

模型可以在一次回复中组合文字气泡、表情回应、普通贴纸和可点击选项。Telemood 通过你已经在使用的 Telegram 客户端按顺序执行这些动作，并记录哪些动作真的成功。

Telemood 不是另一套 Bot 框架或 Telegram 客户端，也不会接管你的 bot token、消息更新循环或部署。

## 回复是什么样子

> 表情回应 → 文字气泡 → 贴纸 → 选项按钮

<img src="assets/telemood-telegram-demo.png" alt="Telegram 实际效果：一次回复按顺序组合文字气泡、表情回应、普通贴纸和选项按钮" width="640">

Telemood 保留计划中的顺序，并记录每个动作的执行结果。

## 核心思路

```text
模型 → 回复计划 → Telemood → 已有 Telegram 客户端 → 执行结果
```

已有的 Bot 或运行时（下文称为**宿主**）仍然决定回复发到哪里、由哪个客户端发送。Telemood 解析模型的计划、绑定可信的宿主上下文，再执行计划。模型不能选择 chat、user、thread、bot namespace、凭据或 Telegram `file_id`。

## 速答

**Telemood 是什么？** 一个小型 Python 库，把模型生成的计划变成更丰富的 Telegram 回复。

**它是另一个 Bot 或客户端吗？** 不是。它复用你已有的 Telegram 客户端和消息更新循环。

**它能做什么？** 有序的文字气泡、普通 Emoji 表情回应、目录中的普通贴纸和一次性选项按钮。

**怎么接入？** 选择同步或异步方式，适配已有客户端，再绑定可信的宿主上下文。准确约定见 [SETUP.zh-CN.md](SETUP.zh-CN.md)。

## 范围与能力

### 组织更丰富的回复

- 文字气泡；长文本按保守规则拆分
- 对触发消息发送普通 Emoji 表情回应
- 发送目录中已登记的静态、动画和视频普通贴纸
- 提供符合 Telegram `callback_data` 64 字节限制的选项按钮

### 安全执行

- 动作按顺序执行；任何非 `VERIFIED` 结果都会停止后续动作。
- 函数调用返回不等于 Telegram 已经成功送达。
- 回复计划使用 `telemood.plan.v1`；未知或不可信输入会被拒绝。
- 可信标识与按钮有效期在解析后由宿主提供。

### 接入已有宿主

- 原生同步和异步执行方式
- 一次性回调，按钮有效期由宿主决定
- 向模型提供安全的贴纸逻辑 ID，而不是 Telegram `file_id`
- 规范化普通贴纸、表情回应变化/计数和基于消息的回调更新

Telemood **不会**运行第二个 Bot 或消息更新循环，不读取或保存 token，不下载 Telegram 媒体，不管理部署；v0.1 也不创建贴纸包或提供 MiniApp 框架。

Python `>=3.11` · 零第三方运行时依赖 · 发行包与导入名 `telemood`

## 给 Agent 的提示词

```text
如有需要，请克隆或拉取 https://github.com/beniedev/telemood。
先只读检查仓库与宿主运行时，报告现有 Telegram 发送路径和可支持的 Telemood 能力。
客户端、bot token、消息更新循环和可信路由标识始终只由宿主持有；不要创建第二套客户端或消息循环。
选择对应的同步或异步 Telemood 接入方式，运行离线测试并构建 wheel。
未经明确人工授权，不要安装、修改宿主、真实发送、重启或部署。
获得授权后，按照 SETUP.zh-CN.md 适配已有客户端。
```

## 状态与验证

Telemood `0.1.0rc1` 是早期预发布版本。核心约定与适配器有离线测试覆盖，但尚未在所有 Telegram SDK 或生产宿主上验证，API 仍可能变化。

内置入站规范化覆盖普通贴纸消息、表情回应变化/计数更新，以及带消息对象的回调。通用文本消息、Telegram Business 消息和 inline 模式回调仍由宿主处理。表情回应更新需要显式订阅，并且可能需要管理员权限。

CI 会在 Python 3.11–3.13 上运行测试、构建 wheel、检查包内容并验证干净安装。CI 验证的是软件包，不是某个具体的生产部署；静态适配器检查也不等于真实 Telegram 验证。

[SETUP.md](SETUP.md) 是权威接入指南。另有[中文接入指南](SETUP.zh-CN.md)、[便携式 Agent skill](skills/telemood/SKILL.md) 和[授权协议](LICENSE)。
