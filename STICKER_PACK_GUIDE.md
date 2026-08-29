# Create a regular sticker pack in Telegram

[简体中文](STICKER_PACK_GUIDE.zh-CN.md) | [Back to README](README.md)

This is the user-side step that comes before Telemood's sticker round trip. Telegram creates and manages the pack; Telemood starts working after someone sends one of its stickers to the integrated bot.

This guide covers **regular stickers made from your own images**.

## Fastest path: use Telegram's built-in editor

1. Open any chat in an up-to-date Telegram app.
2. Open the sticker panel from the message field, then tap **+**.
3. Choose a photo. Telegram can cut out the subject, remove or restore parts of the background, and add text, drawings, or an outline.
4. When the sticker is ready, associate it with one or more emoji. Telegram uses these emoji to suggest the sticker while typing.
5. Choose the option to create a new sticker set, give it a shareable name, and save the sticker to that set. Button wording can vary slightly by Telegram client.
6. Open the chat with your integrated bot and send the sticker from the sticker panel. Sending the source image as a photo or document is not the same as sending a sticker.

Telegram's official walkthrough: [Sticker Editor — Create Your Own Stickers](https://telegram.org/blog/sticker-maker?setln=en).

## Manage the pack with `@Stickers`

Telegram's official [`@Stickers`](https://t.me/Stickers) Mini App is useful when you want to upload prepared artwork or update a pack later:

1. Open `@Stickers` in Telegram and launch the Mini App.
2. Create a new pack, or open one of your existing packs to update it.
3. Choose **stickers**, not emoji, so the result is a regular sticker supported by Telemood v0.1.
4. Use the add-sticker control to upload an image, edit it if needed, and associate it with one or more emoji.
5. Save or publish the changes. You can then send a sticker from the pack or share its `t.me` link.

Exact button labels may change between Telegram clients. The Mini App also provides pack management and usage statistics.

For prepared static artwork uploaded through `@Stickers`:

- use PNG or WEBP;
- make one side exactly 512 pixels and the other side 512 pixels or less;
- use transparency if you want a cut-out sticker;
- keep the source image free of content you do not want others to see.

Telegram also accepts animated and video regular stickers, but they have different file requirements. See [Telegram's current sticker requirements](https://core.telegram.org/stickers) instead of converting them as static images.

## Connect the pack to Telemood

After the pack exists:

1. Send one of its stickers to the bot whose host integrates Telemood.
2. The host routes the incoming regular sticker update to Telemood and may attach a thumbnail or media reference for a vision-capable model.
3. Telemood remembers the sticker under a safe, bot-scoped catalog ID.
4. The model can later choose that catalog ID, and the host's existing Telegram client sends the original sticker back.

Telemood does not upload images, create packs, edit packs, or receive your bot token.

## Sharing and privacy

A regular sticker pack is shareable. Anyone who receives one of its stickers can tap it to view or add the complete pack, and every pack has a unique `t.me` link. Use a pack name and artwork you are comfortable sharing.

Do not put Telegram provider `file_id` values in model prompts or public configuration. Telemood keeps those IDs inside its trusted catalog and transport boundary.

## If it does not work

- **There is no + button:** update Telegram or use the `@Stickers` Mini App.
- **The bot receives a photo instead:** send the finished sticker from the sticker panel, not the original image attachment.
- **Telemood does not catalog it:** v0.1 accepts regular static, animated, and video stickers. Masks and `custom_emoji` are rejected, and the host must route incoming sticker updates.
- **The model cannot understand the picture:** the host must use a vision-capable model directly, or route the sticker image to a vision model and pass its text description to the primary model. Without either path, Telemood deliberately exposes metadata only.
