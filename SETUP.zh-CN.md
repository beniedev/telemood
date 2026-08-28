# Telemood 接入指南

[English](SETUP.md)

本文供 Agent 将 Telemood 接入已有 Telegram bot 宿主。token、client、polling/webhook、thread 路由与重连生命周期始终由宿主唯一持有。

## 1. 修改宿主前

先只读确认：

- 已有发送方法及其 provider 返回值；
- 入站消息、sticker、reaction 与 callback 的路由；
- 是否订阅 reaction update，以及 bot 是否具备所需权限；
- 由宿主持有的 callback/sticker SQLite 状态路径。

不要读取或复制 bot token，不要新建第二个 client 或 update loop。

当前预发行元数据：

    distribution: telemood
    import: telemood
    Python: >=3.11
    runtime dependencies: none

最后输出一份简短的只读报告；不知道时写 `unknown`，不要猜：

    TELEMOOD CAPABILITY REPORT
    Host runtime:            <框架与执行模型>
    Transport ownership:     <client、token、update loop 的 owner>
    Boundary:                sync | async | mixed
    Send methods found:      <现有入口>
    Sticker/callback routes: <路径 | none>
    Reaction subscriptions:  change=<yes|no|unknown>, count=<yes|no|unknown>
    Usable now:              <bubble/reaction/sticker/choices 子集>
    Degraded or missing:     <项目 — 原因>
    Files to modify:         <列表>
    Authorization needed:    <下一项需要授权的准确动作>

## 2. 授权边界

无需 live 授权即可进行：

- 读取仓库和宿主代码、schema、路由、权限与状态路径形状，但不打开 secret 值；
- 在仓库检出中运行合成单元测试并构建 wheel；
- 运行不会调用 transport 的 `check_adapter`；
- 编写能力报告。

安装、修改宿主代码/配置、真实 Telegram 发送、重启/部署，以及任何 push/tag/release，都需要明确人工授权。不明确属于只读或 checkout-local 的动作，一律按需要授权处理。

## 3. 安装

宿主所有者授权修改 Python 环境后，在仓库检出目录执行：

    python -m pip install .
    python -c "import telemood; print(telemood.__version__)"

预期版本为 `0.1.0rc1`。运行时依赖为空，但构建包时仍可能使用常规 Python build requirements。优先正常安装，不要把检出目录手工塞进 `sys.path`。

内置入站规范化目前只覆盖普通贴纸消息、reaction change/count update，以及包含消息对象的 callback。本版本的通用文本消息、Telegram Business 消息与 inline-mode callback 必须由宿主自行路由和规范化。

## 4. 模型计划

模型只能返回 JSON 兼容数据，不能选择 chat、user、thread、bot namespace，不能提供 Telegram file_id、token、endpoint 或 SDK object。

    {
      "version": "telemood.plan.v1",
      "actions": [
        {"type": "bubble", "text": "我正在检查。"},
        {"type": "reaction", "target": "trigger_message", "emoji": "👀"},
        {
          "type": "sticker",
          "sticker": {"kind": "catalog", "id": "sticker_opaque_logical_id"}
        },
        {
          "type": "choices",
          "prompt": "继续吗？",
          "options": [
            {"key": "yes", "label": "继续"},
            {"key": "no", "label": "暂停"}
          ]
        }
      ]
    }

先解析，再绑定宿主可信上下文：

    from telemood import (
        PlanContext,
        bind_interaction_plan,
        parse_interaction_plan,
    )

    typed_plan = parse_interaction_plan(model_output)
    reply = bind_interaction_plan(
        typed_plan,
        PlanContext(
            target=trusted_target,
            authorized_user_id=trusted_user_id,
            bot_namespace=trusted_bot_namespace,
            callback_ttl_seconds=1800.0,
        ),
        sticker_catalog=sticker_catalog,
    )

绑定阶段会按“段落、句子、空白、硬切”的保守启发式自动展开长 bubble；展开后仍保持与 reaction、sticker、choices 的相对顺序。这不是模型级语义理解。

Callback TTL 由宿主通过 `PlanContext` 或 `action_plan_to_reply(..., callback_ttl_seconds=...)` 持有，模型 JSON 不能设置。未知版本、字段、动作类型、catalog ID 与不可信标识都会 fail closed。MiniApp 不属于 v0.1 公开 API。

## 5. 注入已有 transport

使用内置 injected-client adapter，或直接实现 `InteractionHost`。下面的
facade 完整且不绑定 provider：四个宿主 callable 都接收 keyword arguments，
并返回 `InjectedResult`。

    from telemood import InjectedResult, InjectedTelegramAdapter

    class ExistingClientFacade:
        def __init__(self, *, send_message, set_reaction, send_sticker, send_choices):
            self._send_message = send_message
            self._set_reaction = set_reaction
            self._send_sticker = send_sticker
            self._send_choices = send_choices

        def send_message(self, **kwargs):
            return self._send_message(**kwargs)

        def set_reaction(self, **kwargs):
            return self._set_reaction(**kwargs)

        def send_sticker(self, **kwargs):
            return self._send_sticker(**kwargs)

        def send_choices(self, **kwargs):
            return self._send_choices(**kwargs)

    facade = ExistingClientFacade(
        send_message=host_send_message,
        set_reaction=host_set_reaction,
        send_sticker=host_send_sticker,
        send_choices=host_send_choices,
    )
    adapter = InjectedTelegramAdapter(facade)

每个 `host_*` callable 负责把既有 client 操作映射成
`InjectedResult(accepted=..., provider_delivery_id=..., detail=...)`。明确接受映射为 `VERIFIED`，明确拒绝映射为 `FAILED`，非法返回映射为 `UNKNOWN`，超时或副作用不确定的异常映射为 `UNCERTAIN`。不能只因为调用返回就声称成功。

异步宿主提供四个对应的 `async def` facade 方法，并使用
`AsyncInjectedTelegramAdapter` 与 `AsyncInteractionKernel`。不要在已运行的 event loop 内调用 `asyncio.run()` 或 `loop.run_until_complete()`，也不要在 adapter 内另造 event-loop bridge。

只做静态 shape 检查：

    from telemood import check_adapter

    result = check_adapter(adapter, mode="sync")  # 异步使用 mode="async"
    assert result.ok
    assert result.static_only
    assert not result.live_delivery_verified

静态 conformance 不等于真实 Telegram 联调。

Sticker sequence 严格按顺序发送，遇到首个 non-`VERIFIED` 即停止。只有最后一个已尝试 receipt 为 non-`VERIFIED` 时，较短的 receipt 序列才是合法提前停止；全 `VERIFIED` 却缺 receipt、receipt 超量或 non-verified 后继续发送都属于协议错误。

## 6. 入站 regular sticker

内置 normalizer 接收 Telegram Bot API update mapping。宿主提供 bot namespace 与可选的逻辑媒体引用；Telemood 不下载媒体，也不接收 token。

    from telemood import (
        SQLiteStickerCatalog,
        ingest_incoming_sticker,
        normalize_incoming_sticker,
    )

    catalog = SQLiteStickerCatalog("state/stickers.sqlite3")
    event = normalize_incoming_sticker(
        update,
        bot_namespace=trusted_bot_namespace,
        thumbnail_ref=host_thumbnail_ref,
        media_ref=host_media_ref,
    )
    model_event = ingest_incoming_sticker(event, catalog)

`model_event.sticker` 包含 opaque catalog ID、规范化文本与可选逻辑媒体引用；event 还提供 sender kind、target role、thread presence 与发生时间，但不暴露可复用的 provider `file_id`。没有媒体引用时，`model_event.sticker.text` 会明确说明未附加图像；模型只看到了 metadata。

v0.1 支持 static、animated、video 三种 format 的 regular sticker；mask 与 custom_emoji type 会被拒绝，不会进入 catalog。

## 7. 入站与出站 reaction

宿主明确报告能力前，reaction 发送默认关闭：

    from telemood import (
        InteractionCapabilities,
        normalize_incoming_reaction_change,
        normalize_incoming_reaction_count,
    )

    capabilities = InteractionCapabilities(
        can_send_reactions=True,
        can_receive_reaction_changes=True,
        can_receive_reaction_counts=True,
        message_reaction_subscribed=True,
        message_reaction_count_subscribed=True,
        available_reactions=("👍", "👀"),
        reaction_change_unavailable_reason=None,
        reaction_count_unavailable_reason=None,
    )

分别规范化 `message_reaction` 与 `message_reaction_count` update：

    change = normalize_incoming_reaction_change(update)
    count = normalize_incoming_reaction_count(update)

Change update 保留 actor 与 old/new reaction set；count update 是匿名聚合，可能延迟送达。两者的订阅状态与不可用原因独立。`ReactionValue` 可表示 emoji、custom emoji 与 paid reaction；v0.1 执行层只接受普通 emoji。`InteractionKernel.accept_incoming_reaction` 会返回明确的接受/拒绝原因和可选 capability detail。

Telegram reaction update 必须由宿主显式订阅，并可能要求 bot 具备管理员权限。bot 自己发送 reaction 后不得伪造入站 update。

## 8. Callback 与执行

需要跨重启保留 callback 时，使用由宿主持有的持久 store，并选择与 adapter 模式一致的 kernel：

    from telemood import (
        AsyncInteractionKernel,
        InteractionKernel,
        SQLiteCallbackStore,
        normalize_callback_query,
    )

    kernel = InteractionKernel(
        adapter,
        callbacks=SQLiteCallbackStore("state/callbacks.sqlite3"),
        sticker_catalog=catalog,
    )
    receipt = kernel.execute_reply(
        reply,
        request_id=trusted_request_id,
        capabilities=capabilities,
    )

    async_kernel = AsyncInteractionKernel(
        async_adapter,
        callbacks=SQLiteCallbackStore("state/callbacks.sqlite3"),
        sticker_catalog=catalog,
    )
    async_receipt = await async_kernel.execute_reply(
        reply,
        request_id=trusted_request_id,
        capabilities=capabilities,
    )

动作严格按 plan 顺序逐项等待 receipt。遇到 FAILED、UNKNOWN 或 UNCERTAIN 立即停止。最终 receipt 包含 action receipts、停止位置和未执行数量；兼容 multi-part sticker 请求会保留每个完整 TransportReceipt。

Choices callback 绑定 user/chat/thread、宿主持有的 TTL、pending/active 状态与 one-shot 消费。handle 受 Telegram `callback_data` 的 64 UTF-8 byte 限制。通过同一个 kernel/store 规范化并消费 callback：

    callback = normalize_callback_query(update)
    resolution = kernel.consume_callback(
        callback.token,
        user_id=callback.user_id,
        chat_id=callback.target.chat_id,
        thread_id=callback.target.thread_id,
    )

成功创建 choices 后，`InteractionReceipt.callback_expires_at` 会给出所有
active callback handle 中最早的绝对 Unix 到期时间。宿主可将它与
`provider_delivery_id` 交给自己已有的 scheduler 和 Telegram client，按时移除或
禁用过期的 reply markup；Telemood 自身不会启动 scheduler、线程、event loop
或第二个 client。

Callback store 仍是到期安全判断的唯一权威：即使界面清理尚未执行或执行失败，
过期点击也会 fail closed。已有自定义 `CallbackStore` 继续返回
`CallbackToken(value)` 即可兼容；只有同时填写可选的 `expires_at` 元数据时，
receipt 才会提供对应到期时间，否则为 `None`。

## 9. 最小离线验证

安装前先在仓库检出中运行，修改后再重复：

    python -m unittest discover -s tests -v
    python -m pip wheel . --no-deps -w dist

测试只使用合成数据。真实发送、宿主修改、重启、部署、push、tag 或 release 都需要单独获得人工授权。
