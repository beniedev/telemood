# Telemood 集成与配置指南

本文档是将 `hermes_telegram_interaction` 接入已有 Telegram 主机 Agent 的权威说明。
同时适用于人类执行者与编码 Agent。

仓库地址：`https://github.com/beniedev/telemood`

## 0）授权边界（必须）

- 可先执行只读检查、静态校验与离线合成测试。
- 宿主始终是其已配置 bot token 的唯一持有方。Telemood/适配器集成仅复用宿主现有 Telegram 传输层/客户端，不得请求、读取、复制或持久化 bot token 或任何 raw SDK 句柄。
- 未经明确人工授权前，不得改动宿主代码/配置、发起线上发送、重启或部署。
- 上线前需给出能力缺口报告。

## 1）先决条件

- Python 版本：`>=3.11`
- 发行包/导入名：`hermes-telegram-interaction`（`import hermes_telegram_interaction`）
- `dependencies = []` 表示运行时零依赖；不等于“零构建/环境工具”。
- 本包不持有 Hermes 依赖，也不直接拥有 Telegram 传输能力。
- 本版本不负责自定义贴纸生成或自建贴纸集发布。

已克隆仓库并授权变更后，使用宿主目标 Python 环境执行：

```bash
python -m pip install .
```

## 2）宿主环境只读勘测

接入前确认：

1. 宿主已持有 Telegram 客户端、token 与 SDK 生命周期。
2. 定位消息、回调、反应、贴纸的上行路径。
3. 找到可安全同步调用的适配器边界。
4. 选择宿主已有状态路径（例如 `state/callbacks.sqlite3`、`state/stickers.sqlite3`）。

## 3）目标与授权边界

`action_plan_to_reply` 支持输入：

- `{"actions": [...]}`
- `[...]`

`target` 与 `authorized_user_id` 仅由宿主可信注入；模型不能设置。

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
        {"kind": "bubble", "text": "正在处理请求"},
        {"kind": "reaction", "emoji": "👀"},
        {"kind": "sticker", "sticker_ref": "known-regular-sticker-ref"},
        {"kind": "bubble", "text": "我将继续处理下一步"},
        {"kind": "choices", "prompt": "继续吗？", "options": [{"key": "yes", "label": "继续"}, {"key": "no", "label": "暂停"}], "callback_ttl_seconds": 1200},
    ]
}

reply = action_plan_to_reply(
    plan,
    target=trusted_target,
    authorized_user_id="trusted-user-id",
)
```

`kind` 是动作鉴别字段（`bubble`、`reaction`、`sticker`、`choices`）。
模型不得注入 `target`、`request_id`、token、endpoint 或授权用户字段。

贴纸动作只能是 `{"kind": "sticker", "sticker_ref": "<逻辑贴纸引用>"}`。
`sticker_ref` 必须是宿主或目录已允许的逻辑/文件引用，不是 endpoint。
模型不能伪造 `target/user/token/endpoint` 字段。
`action_plan_to_reply` 不会校验 catalog 成员关系。

## 4）精确同步适配器协议

宿主适配器需实现以下同步方法：

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

`DeliveryStatus` 在当前版本的含义为：

- `VERIFIED`：已明确成功
- `FAILED`：明确失败
- `UNKNOWN`：无法确认
- `UNCERTAIN`：未完全确认/部分完成

`check_adapter` 仅做静态形状检查，不调用线上传输。

```python
from hermes_telegram_interaction import check_adapter

result = check_adapter(adapter)
```

- `result.ok` 是公开成功标记（`result.passed` 等价）。
- `result.static_only` 需为 `True`。
- `result.live_delivery_verified` 在 `check_adapter` 中始终为 `False`，因为它仅做静态校验；授权线上探针会产出独立证据，不会变更该结果。
- `result.issues` 列出不兼容方法。

## 5）内核与状态初始化

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

`sticker_catalog` 由宿主持有，并按宿主路径策略持久化。

## 6）普通贴纸收发流程（准确路径）

Telemood 不下载媒体，不创建自定义贴纸，也不构建贴纸集合。

### 6.1 上行贴纸归一化与登记

```python
from hermes_telegram_interaction import IncomingSticker

# 伪代码
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

### 6.2 按模型输出发送贴纸引用

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

模型只能返回宿主已知的 `sticker_ref` 逻辑名，`kernel.send_seen_sticker` 可按入站标识回放可见贴纸。

### 6.3 按入站唯一标识回放已见贴纸

```python
kernel.send_seen_sticker(
    target=TargetRef(channel="telegram", chat_id="chat-id", message_id="message-id"),
    bot_namespace="bot-namespace",
    file_unique_id="file-unique-id",
)
```

`send_seen_sticker` 会在 `sticker_catalog` 中按 `bot_namespace + file_unique_id` 映射到可发送 `file_id`。

## 7）文本切分必须显式调用

`split_semantic_bubbles` **不会自动触发**。

```python
from hermes_telegram_interaction import split_semantic_bubbles

bubbles = split_semantic_bubbles(long_text, max_length=4096)
```

## 8）执行与能力上报

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

`InteractionCapabilities` 字段为：

- `can_send_reactions`
- `can_receive_reactions`
- `available_reactions`

## 9）上行事件路由

### 9.1 Callback（内联）

```python
from hermes_telegram_interaction import CallbackToken

# 伪代码
resolution = kernel.consume_callback(
    CallbackToken(raw_callback_data),
    user_id="trusted-user-id",
    chat_id="chat-id",
    thread_id="thread-id",  # 可选
)
if resolution.accepted:
    action_key = resolution.payload.value
else:
    failure = resolution.reason
```

### 9.2 Sticker（上行）

参考 6.1 节“普通贴纸收发流程”中的入站归一化流程。

### 9.3 Reaction（上行）

```python
from hermes_telegram_interaction import (
    InteractionCapabilities,
    IncomingReaction,
    InteractionKernel,
    TargetRef,
)

# 伪代码
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

可用上行反应事件需包含 `target` + `emoji` + `user_id`。

## 10）回调解析返回值

`CallbackResolution` 字段固定为：

- `accepted`
- `reason`（来自 `CallbackRejection`）
- `payload`（`CallbackPayload`）

## 11）三层验收

### 一级：适配器静态检查

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

### 二级：完整本地回归

```bash
python -m unittest discover -s tests -p "test*.py" -v
```

### 三级：独立授权线上探针

只允许在明确授权后进行一次最小线上探针。静态检查和离线测试不等同于线上发送成功证明。

## 12）能力报告模板

```markdown
### Telemood 适配器能力报告
- Host 传输：<SDK + 版本>
- Python：<版本>
- send_bubble：支持 / 不支持
- send_reaction：支持 / 不支持
- send_choices：支持 / 不支持
- send_sticker_sequence：支持 / 不支持
- can_send_reactions：true/false
- can_receive_reactions：true/false
- available_reactions：["👍", ...]
- state 文件：`state/callbacks.sqlite3`, `state/stickers.sqlite3`
- check_adapter passed：true/false
- check_adapter static_only：true
- check_adapter live_delivery_verified：false
- issues：[]
```

能力报告应如实写明降级，不得把“失败/缺失”写成“全部成功”。

## 13）回滚与状态保留

1. 先停用旧 hook/路由。
2. 经明确授权后重启宿主进程（如有必要）。
3. 保留并保全 `state/callbacks.sqlite3` 与 `state/stickers.sqlite3`，回滚时不得删除。
