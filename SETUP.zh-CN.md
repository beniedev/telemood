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

## 2. 模型计划

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
        ),
        sticker_catalog=sticker_catalog,
    )

绑定阶段会按“段落、句子、空白、硬切”的保守启发式自动展开长 bubble；展开后仍保持与 reaction、sticker、choices 的相对顺序。这不是模型级语义理解。

未知版本、字段、动作类型、catalog ID 与不可信标识都会 fail closed。

## 3. 注入已有 transport

围绕宿主已经创建的 client 实现四个同步 InteractionHost 方法：

    from telemood import DeliveryStatus, TransportReceipt

    class ExistingClientAdapter:
        def __init__(self, existing_client):
            self.client = existing_client

        def send_bubble(self, request_id, request):
            result = self.client.send_message(request.target, request.text)
            return TransportReceipt(
                DeliveryStatus.VERIFIED,
                provider_delivery_id=str(result.message_id),
            )

        def send_reaction(self, request_id, request):
            ...

        def send_sticker_sequence(self, request_id, request, parts):
            # 每个实际尝试的 part 按顺序返回一个 TransportReceipt。
            ...

        def send_choices(self, request_id, request, callback_tokens):
            ...

示例刻意不绑定 SDK。只有 provider 明确确认副作用时才能返回 VERIFIED；明确拒绝映射为 FAILED，无法理解的返回映射为 UNKNOWN，超时或副作用状态不确定映射为 UNCERTAIN。

只做静态 shape 检查：

    from telemood import check_adapter

    result = check_adapter(adapter)
    assert result.ok
    assert result.static_only
    assert not result.live_delivery_verified

静态 conformance 不等于真实 Telegram 联调。

## 4. 入站 regular sticker

宿主负责规范化 Telegram sticker，也可以附加逻辑 media reference。Core 不下载媒体，也不接收 token。

    from telemood import (
        IncomingSticker,
        IncomingStickerEvent,
        SQLiteStickerCatalog,
        StickerFormat,
        StickerType,
        ingest_incoming_sticker,
    )

    catalog = SQLiteStickerCatalog("state/stickers.sqlite3")
    event = IncomingStickerEvent(
        target=trusted_target,
        sender_user_id=trusted_sender_id,
        received_at=provider_timestamp,
        sticker=IncomingSticker(
            bot_namespace=trusted_bot_namespace,
            file_id=provider_sticker.file_id,
            file_unique_id=provider_sticker.file_unique_id,
            type=StickerType.REGULAR,
            format=StickerFormat.ANIMATED,
            emoji=provider_sticker.emoji,
            set_name=provider_sticker.set_name,
            thumbnail_ref=host_thumbnail_ref,
            media_ref=host_media_ref,
        ),
    )
    model_view = ingest_incoming_sticker(event, catalog)

model_view 只包含 opaque catalog ID、规范化文本和可选的逻辑媒体引用，不包含可复用的 provider file_id。没有媒体引用时，文本会明确说明未附加图像内容。

v0.1 支持 static、animated、video 三种 format 的 regular sticker；mask 与 custom_emoji type 会被拒绝，不会进入 catalog。

## 5. 入站与出站 reaction

宿主明确报告能力前，reaction 发送默认关闭：

    from telemood import InteractionCapabilities

    capabilities = InteractionCapabilities(
        can_send_reactions=True,
        can_receive_reaction_changes=True,
        can_receive_reaction_counts=True,
        reaction_updates_subscribed=True,
        available_reactions=("👍", "👀"),
    )

有 actor 的 old/new reaction set 使用 IncomingReactionChange；匿名聚合计数使用独立的 IncomingReactionCount。ReactionValue 能准确表示 emoji、custom_emoji 与 paid；v0.1 执行层只接受普通 emoji。InteractionKernel.accept_incoming_reaction 返回带明确原因的 ReactionAcceptance，不会把 unavailable、not subscribed、unsupported 都静默变成 None。

Telegram reaction update 必须由宿主显式订阅，并可能要求 bot 具备管理员权限。bot 自己发送 reaction 后不得伪造入站 update。

## 6. Callback 与执行

需要跨重启保留 callback 时，使用由宿主持有的持久 store：

    from telemood import InteractionKernel, SQLiteCallbackStore

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

动作严格按 plan 顺序逐项等待 receipt。遇到 FAILED、UNKNOWN 或 UNCERTAIN 立即停止。最终 receipt 包含 action receipts、停止位置和未执行数量；兼容 multi-part sticker 请求会保留每个完整 TransportReceipt。

Choices callback 绑定 user/chat/thread、TTL、pending/active 状态与 one-shot 消费。handle 受 Telegram callback_data 的 64 UTF-8 byte 限制。

## 7. 最小离线验证

    python -m unittest discover -s tests -v
    python -m pip wheel . --no-deps -w dist

测试只使用合成数据。真实发送、宿主修改、重启、部署、push、tag 或 release 都需要单独获得人工授权。
