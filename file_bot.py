import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# ================= 配置区域 =================
# 1. 填入 BotFather 给你的 Token
TOKEN = "8504811149:AAELbMB9KKeYmyjdY4XiaR7d1afE2g2ZsnY"

# 2. 填入你想保存文件的服务器绝对路径 (确保文件夹存在，或者脚本有权限创建)
# Windows 示例: r"C:\Users\Admin\Downloads\TelegramFiles"
# Linux 示例: "/home/user/downloads/telegram_files"
SAVE_PATH = "/root/blog-autopilot/input"

# 3. 填入你的 Telegram User ID (数字)，防止他人滥用
ADMIN_ID = 7465144093 
# ===========================================

# 设置日志，方便调试
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- 1. 安全检查 ---
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 你没有权限使用此机器人。")
        return

    # --- 2. 获取文件信息 ---
    document = update.message.document
    file_name = document.file_name
    file_id = document.file_id
    
    # 获取文件后缀名，判断是否为目标格式 (Word, PDF, Markdown)
    # Markdown 文件通常是 .md 或 .markdown
    allowed_extensions = ('.doc', '.docx', '.pdf', '.md', '.markdown')
    
    if not file_name.lower().endswith(allowed_extensions):
        await update.message.reply_text(f"⚠️ 忽略文件: {file_name} (格式不符合要求)")
        return

    # --- 3. 下载文件 ---
    try:
        # 确保保存目录存在
        if not os.path.exists(SAVE_PATH):
            os.makedirs(SAVE_PATH)

        # 获取文件对象
        new_file = await context.bot.get_file(file_id)
        
        # 拼接完整保存路径
        save_location = os.path.join(SAVE_PATH, file_name)
        
        # 下载并保存
        await new_file.download_to_drive(save_location)
        
        await update.message.reply_text(f"✅ 文件已保存: {file_name}")
        print(f"成功保存文件: {save_location}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ 下载失败: {e}")
        print(f"错误: {e}")

if __name__ == '__main__':
    # 创建应用
    application = ApplicationBuilder().token(TOKEN).build()

    # 添加处理器：只监听文档类型的消息
    # filters.Document.ALL 会捕获所有文件，我们在函数内部再过滤后缀
    file_handler = MessageHandler(filters.Document.ALL, handle_document)
    application.add_handler(file_handler)

    print("🤖 机器人已启动，正在监听文件...")
    # 运行机器人
    application.run_polling()