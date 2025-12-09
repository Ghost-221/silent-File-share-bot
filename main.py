import logging
import sqlite3
import uuid
import os
import asyncio  # এনিমেশনের জন্য টাইম ডিলে দিতে এটি লাগবে
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- ১. ফ্লাস্ক সার্ভার (বট সজাগ রাখার জন্য) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running with Animation!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- ২. কনফিগারেশন ---
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
admin_env = os.environ.get("ADMIN_IDS", "123456789") 
ADMIN_IDS = [int(x.strip()) for x in admin_env.split(',')]

# --- ৩. লগিং ও ডাটাবেস ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def init_db():
    conn = sqlite3.connect('files.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS files (
            unique_code TEXT PRIMARY KEY,
            file_id TEXT,
            file_type TEXT,
            password TEXT,
            limit_count INTEGER,
            usage_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- ৪. বটের লজিক ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args

    # সাধারণ ইউজার লিংক দিয়ে আসলে
    if args:
        unique_code = args[0]
        
        # --- লোডিং এনিমেশন ১ (পাসওয়ার্ড চাওয়ার আগে) ---
        loading_msg = await update.message.reply_text("⏳ <b>ফাইল লোডিং হচ্ছে...</b>", parse_mode='HTML')
        await asyncio.sleep(1.5) # ১.৫ সেকেন্ড অপেক্ষা করবে (এনিমেশন ভাব আনার জন্য)
        
        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        conn.close()

        if result:
            limit_count, usage_count = result
            if usage_count >= limit_count:
                 await loading_msg.edit_text("❌ দুঃখিত! এই ফাইলটির ডাউনলোড লিমিট শেষ।")
            else:
                context.user_data['attempting_code'] = unique_code
                # আগের মেসেজ এডিট করে পাসওয়ার্ড চাইবে
                await loading_msg.edit_text(f"🔒 ফাইলটি পেতে পাসওয়ার্ড দিন:\n(বাকি আছে: {limit_count - usage_count} জন)")
        else:
            await loading_msg.edit_text("❌ লিংকটি ভুল বা মেয়াদোত্তীর্ণ।")

    # স্টার্ট কমান্ড দিলে (এডমিন চেকিং)
    else:
        if user_id in ADMIN_IDS:
            await update.message.reply_text(f"স্বাগতম এডমিন (ID: {user_id})! 👑\nফাইল আপলোড করুন।")
        else:
            await update.message.reply_text("ফাইল পেতে হলে সঠিক লিংক ব্যবহার করুন।")

async def handle_files_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return 

    file_id, file_type = None, None

    if update.message.document:
        file_id = update.message.document.file_id
        file_type = 'document'
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = 'video'
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = 'photo'
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = 'audio'

    if file_id:
        context.user_data['uploading_file_id'] = file_id
        context.user_data['uploading_file_type'] = file_type
        await update.message.reply_text(
            "✅ ফাইল পেয়েছি!\n"
            "ফরম্যাট: `পাসওয়ার্ড` `লিমিট`\n"
            "উদাহরণ: `video123 20`"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # --- ১. এডমিন পাসওয়ার্ড সেট করছে ---
    if user_id in ADMIN_IDS and 'uploading_file_id' in context.user_data:
        try:
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ ভুল! লিখুন: `pass` `limit` (যেমন: `abc 5`)")
                return
            
            password = parts[0]
            limit_count = int(parts[1])

            file_id = context.user_data['uploading_file_id']
            file_type = context.user_data['uploading_file_type']
            unique_code = str(uuid.uuid4())[:8]

            conn = sqlite3.connect('files.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("INSERT INTO files (unique_code, file_id, file_type, password, limit_count, usage_count) VALUES (?, ?, ?, ?, ?, ?)", 
                      (unique_code, file_id, file_type, password, limit_count, 0))
            conn.commit()
            conn.close()

            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={unique_code}"
            
            await update.message.reply_text(
                f"✅ **লিংক তৈরি হয়েছে!**\n"
                f"🔑 পাস: `{password}`\n"
                f"👥 লিমিট: {limit_count}\n"
                f"🔗 লিংক: {link}", 
                parse_mode='Markdown'
            )
            del context.user_data['uploading_file_id']
        
        except ValueError:
            await update.message.reply_text("❌ লিমিট অবশ্যই ইংরেজি সংখ্যা হতে হবে।")
        return

    # --- ২. ইউজার পাসওয়ার্ড দিচ্ছে ---
    if 'attempting_code' in context.user_data:
        user_pass = text
        unique_code = context.user_data['attempting_code']
        
        # --- লোডিং এনিমেশন ২ (পাসওয়ার্ড চেক করার সময়) ---
        status_msg = await update.message.reply_text("🔄 <b>পাসওয়ার্ড যাচাই করা হচ্ছে...</b>", parse_mode='HTML')
        await asyncio.sleep(1) # ১ সেকেন্ড ওয়েট

        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT file_id, file_type, password, limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        
        if result:
            file_id, file_type, db_pass, limit_count, usage_count = result
            
            if usage_count >= limit_count:
                await status_msg.edit_text("❌ দুঃখিত, লিমিট শেষ হয়ে গেছে।")
                conn.close()
                del context.user_data['attempting_code']
                return

            if user_pass == db_pass:
                # সঠিক হলে লোডিং এনিমেশন পরিবর্তন হবে
                await status_msg.edit_text("✅ <b>সঠিক পাসওয়ার্ড! ফাইল আপলোড হচ্ছে... 📤</b>", parse_mode='HTML')
                await asyncio.sleep(1) # ফাইল পাঠানোর আগে একটু বিরতি (ন্যাচারাল ভাব আনার জন্য)
                
                if file_type == 'document': await context.bot.send_document(user_id, file_id)
                elif file_type == 'video': await context.bot.send_video(user_id, file_id)
                elif file_type == 'photo': await context.bot.send_photo(user_id, file_id)
                elif file_type == 'audio': await context.bot.send_audio(user_id, file_id)

                new_usage = usage_count + 1
                c.execute("UPDATE files SET usage_count=? WHERE unique_code=?", (new_usage, unique_code))
                conn.commit()
                del context.user_data['attempting_code']
            else:
                await status_msg.edit_text("❌ ভুল পাসওয়ার্ড! আবার চেষ্টা করুন।")
        else:
            await status_msg.edit_text("❌ লিংক কাজ করছে না।")
        
        conn.close()
        return

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.ATTACHMENT | filters.PHOTO, handle_files_admin))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.run_polling()
