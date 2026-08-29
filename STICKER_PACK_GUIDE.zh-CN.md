# 在 Telegram 中用自己的图片制作普通贴纸包

[English](STICKER_PACK_GUIDE.md) | [返回 README](README.zh-CN.md)

这是使用 Telemood 贴纸闭环前的用户侧准备。贴纸包由 Telegram 创建和管理；用户把包里的一张贴纸发给已经接入 Telemood 的 Bot 后，Telemood 才开始处理。

本指南只讲**用自己的图片制作普通贴纸（regular sticker）**。

## 最快的方法：使用 Telegram 内置编辑器

1. 在更新到较新版本的 Telegram 中打开任意聊天。
2. 从消息输入框打开贴纸面板，再点 **+**。
3. 选择一张照片。Telegram 可以自动抠出主体，也可以手动擦除或恢复背景，并添加文字、涂鸦或描边。
4. 贴纸完成后，为它选择一个或多个能表达含义的 Emoji。以后输入这些 Emoji 时，Telegram 可以推荐这张贴纸。
5. 选择新建贴纸包，填写一个适合公开分享的名称，再把贴纸保存到这个包。不同 Telegram 客户端的按钮文字可能略有差异。
6. 打开已经接入 Telemood 的 Bot 对话，从贴纸面板发送刚才的贴纸。直接把原图作为照片或文件发送，不算发送贴纸。

Telegram 官方图文说明：[Sticker Editor — Create Your Own Stickers](https://telegram.org/blog/sticker-maker?setln=en)。

## 使用 `@Stickers` 管理贴纸包

如果想上传提前处理好的图片，或者以后继续更新贴纸包，可以使用 Telegram 官方 [`@Stickers`](https://t.me/Stickers) Mini App：

1. 在 Telegram 中打开 `@Stickers` 并启动 Mini App。
2. 新建一个贴纸包，或者打开自己已有的贴纸包进行更新。
3. 选择 **stickers** 而不是 emoji，生成 Telemood v0.1 支持的普通贴纸。
4. 使用添加贴纸的入口上传图片；按需编辑后，为它关联一个或多个 Emoji。
5. 保存或发布改动。之后可以直接发送包里的贴纸，也可以分享它的 `t.me` 链接。

不同 Telegram 客户端的按钮文字可能变化。Mini App 还提供贴纸包管理和使用统计。

如果要通过 `@Stickers` 上传提前处理好的静态图片：

- 使用 PNG 或 WEBP 格式；
- 一条边必须正好为 512 像素，另一条边不得超过 512 像素；
- 想要抠图效果时使用透明背景；
- 不要在素材里放入不希望其他人看到的内容。

Telegram 也接受 animated 和 video 类型的普通贴纸，但文件要求不同。请直接查看 [Telegram 当前的贴纸格式要求](https://core.telegram.org/stickers)，不要按静态图片的方法转换。

## 让 Telemood 收藏这张贴纸

贴纸包创建好以后：

1. 把包里的一张贴纸发给已经接入 Telemood 的 Bot。
2. 宿主把这条普通贴纸更新交给 Telemood；如果模型支持视觉，宿主还可以附加缩略图或媒体引用。
3. Telemood 用一个安全且按 Bot 隔离的目录 ID 收藏贴纸。
4. 模型以后可以选择这个目录 ID，再由宿主已有的 Telegram 客户端发回原贴纸。

Telemood 不负责上传图片、创建或修改贴纸包，也不会接收你的 bot token。

## 分享与隐私

普通贴纸包是可以分享的。收到其中一张贴纸的人可以点开并添加整个贴纸包；每个包也有唯一的 `t.me` 链接。因此，包名和素材应当只使用你愿意分享的内容。

不要把 Telegram provider `file_id` 写进模型提示词或公开配置。Telemood 会把这些 ID 留在可信目录和传输边界内。

## 遇到问题

- **贴纸面板里没有 +：** 更新 Telegram，或者改用 `@Stickers` Mini App。
- **Bot 收到的是图片：** 请从贴纸面板发送制作完成的贴纸，不要发送原始图片附件。
- **Telemood 没有收藏：** v0.1 接受普通 static、animated 和 video sticker；mask 与 `custom_emoji` 会被拒绝，而且宿主必须路由入站贴纸更新。
- **模型看不懂图片内容：** 宿主需要直接使用视觉模型，或者把贴纸图片路由给视觉模型，再把文字描述交给主模型理解；两种路径都没有时，Telemood 会刻意只提供元数据。
