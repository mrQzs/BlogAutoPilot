import asyncio
import json
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".doc", ".docx", ".pdf", ".md", ".markdown", ".txt")


def load_bots_from_config():
    """从 categories.json 读取 bot 配置列表"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    bot_cfg = config.get("_bots", {})
    admin_id = bot_cfg.get("admin_id")
    base_input = bot_cfg.get("main_save_path", "/root/BlogAutoPilot/input")

    bots = []

    # 主 bot（接收文件到 input 根目录）
    main_token = bot_cfg.get("main_token")
    if main_token:
        bots.append({
            "token": main_token,
            "save_path": base_input,
            "name": "MainBot",
        })

    # 子分类 bot（有 bot_token 字段的子类）
    for category, subs in config.items():
        if category.startswith("_") or not isinstance(subs, list):
            continue
        for sub in subs:
            token = sub.get("bot_token")
            if token:
                name = f"{category}_{sub['name']}Bot"
                save_path = os.path.join(
                    base_input, category, f"{sub['name']}_{sub['id']}"
                )
                bots.append({
                    "token": token,
                    "save_path": save_path,
                    "name": name,
                })

    return admin_id, bots


def make_handler(save_path: str, bot_name: str, admin_id: int):
    """为每个 bot 创建独立的文件处理函数"""

    async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != admin_id:
            await update.message.reply_text("你没有权限使用此机器人。")
            return

        document = update.message.document
        file_name = document.file_name

        if not file_name.lower().endswith(ALLOWED_EXTENSIONS):
            await update.message.reply_text(
                f"忽略文件: {file_name} (格式不支持)"
            )
            return

        try:
            os.makedirs(save_path, exist_ok=True)
            new_file = await context.bot.get_file(document.file_id)
            save_location = os.path.join(save_path, file_name)
            await new_file.download_to_drive(save_location)
            await update.message.reply_text(f"[{bot_name}] 文件已保存: {file_name}")
            logger.info(f"[{bot_name}] 保存文件: {save_location}")
        except Exception as e:
            await update.message.reply_text(f"下载失败: {e}")
            logger.error(f"[{bot_name}] 错误: {e}")

    return handle_document


async def main():
    admin_id, bots = load_bots_from_config()

    if not bots:
        logger.error("categories.json 中未配置任何 bot")
        return

    started = []
    for bot_cfg in bots:
        name = bot_cfg["name"]
        try:
            app = ApplicationBuilder().token(bot_cfg["token"]).build()
            handler = make_handler(bot_cfg["save_path"], name, admin_id)
            app.add_handler(MessageHandler(filters.Document.ALL, handler))
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            started.append((app, name))
            logger.info(f"[{name}] 已启动 -> {bot_cfg['save_path']}")
        except Exception as e:
            logger.error(f"[{name}] 启动失败: {e}")

    if not started:
        logger.error("所有 bot 启动失败，退出")
        return

    logger.info(f"共 {len(started)} 个机器人运行中")

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        for app, name in started:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            logger.info(f"[{name}] 已停止")


if __name__ == "__main__":
    asyncio.run(main())
