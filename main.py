import logging
import sqlite3
import uuid
import os
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- ১. ফ্লাস্ক সার্ভার (বট সজাগ রাখার জন্য) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running with Download Limit feature!"

def run_http():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- ২. কনফিগারেশন ---
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789")) 

# --- ৩. লগিং ও ডাটাবেস ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def init_db():
    conn = sqlite3.connect('files.db', check_same_thread=False)
    c = conn.cursor()
    # আগের টেবিল থাকলে নতুন কলাম যুক্ত করার ঝামেলা এড়াতে আমরা নতুন টেবিল স্ট্রাকচার চেক করব
    # রেন্ডারে ডাটা রিসেট হয় তাই সমস্যা নেই, লোকাল পিসিতে হলে আগের files.db ডিলিট করে রান করবেন।
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

    # ইউজার যখন লিংকে ক্লিক করবে
    if args:
        unique_code = args[0]
        
        # ডাটাবেস চেক করা: লিমিট শেষ কিনা
        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        conn.close()

        if result:
            limit_count, usage_count = result
            # লিমিট চেক
            if usage_count >= limit_count:
                 await update.message.reply_text("❌ দুঃখিত! এই ফাইলটির ডাউনলোড লিমিট শেষ হয়ে গেছে।")
            else:
                context.user_data['attempting_code'] = unique_code
                await update.message.reply_text(f"🔒 ফাইলটি খোলার জন্য পাসওয়ার্ড দিন:\n(বাকি আছে: {limit_count - usage_count} জন)")
        else:
            await update.message.reply_text("❌ লিংকটি ভুল বা মেয়াদোত্তীর্ণ।")

    # সাধারণ স্টার্ট মেসেজ
    else:
        if user_id == ADMIN_ID:
            await update.message.reply_text("স্বাগতম এডমিন! 👑\nযেকোনো ফাইল সেন্ড করুন লিংক তৈরি করার জন্য।")
        else:
            await update.message.reply_text("ফাইল পেতে হলে সঠিক লিংক ব্যবহার করুন।")

async def handle_files_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # শুধুমাত্র এডমিন চেক
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ আপনি এই বটের এডমিন নন।")
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
            "✅ ফাইল রিসিভ হয়েছে!\n\n"
            "এখন **পাসওয়ার্ড** এবং **লিমিট** স্পেস দিয়ে লিখে সেন্ড করুন।\n"
            "ফরম্যাট: `পাসওয়ার্ড` `লিমিট`\n\n"
            "উদাহরণ: `pass123 10` (মানে ১০ জন ডাউনলোড করতে পারবে)"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # --- ১. এডমিন পাসওয়ার্ড এবং লিমিট সেট করছে ---
    if user_id == ADMIN_ID and 'uploading_file_id' in context.user_data:
        try:
            # টেক্সট থেকে পাসওয়ার্ড এবং লিমিট আলাদা করা
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ ভুল ফরম্যাট! দয়া করে পাসওয়ার্ড এবং সংখ্যা স্পেস দিয়ে লিখুন।\nউদাহরণ: `mypass 5`")
                return
            
            password = parts[0]
            limit_count = int(parts[1]) # সংখ্যায় কনভার্ট করা

            file_id = context.user_data['uploading_file_id']
            file_type = context.user_data['uploading_file_type']
            unique_code = str(uuid.uuid4())[:8]

            # ডাটাবেসে সেভ
            conn = sqlite3.connect('files.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("INSERT INTO files (unique_code, file_id, file_type, password, limit_count, usage_count) VALUES (?, ?, ?, ?, ?, ?)", 
                      (unique_code, file_id, file_type, password, limit_count, 0))
            conn.commit()
            conn.close()

            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={unique_code}"
            
            await update.message.reply_text(
                f"✅ **লিংক তৈরি সফল!**\n\n"
                f"🔑 পাসওয়ার্ড: `{password}`\n"
                f"👥 ডাউনলোড লিমিট: {limit_count} জন\n"
                f"🔗 লিংক: {link}", 
                parse_mode='Markdown'
            )
            
            # মেমোরি ক্লিয়ার
            del context.user_data['uploading_file_id']
        
        except ValueError:
            await update.message.reply_text("❌ লিমিট অবশ্যই একটি সংখ্যা হতে হবে (যেমন: 10)। আবার চেষ্টা করুন।")
        return

    # --- ২. ইউজার পাসওয়ার্ড দিচ্ছে ---
    if 'attempting_code' in context.user_data:
        user_pass = text
        unique_code = context.user_data['attempting_code']

        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT file_id, file_type, password, limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        
        if result:
            file_id, file_type, db_pass, limit_count, usage_count = result
            
            # আবার লিমিট চেক (যদি ইতিমধ্যে কেউ নিয়ে ফেলে)
            if usage_count >= limit_count:
                await update.message.reply_text("❌ দুঃখিত! এই ফাইলের লিমিট শেষ হয়ে গেছে।")
                conn.close()
                del context.user_data['attempting_code']
                return

            if user_pass == db_pass:
                # ইউজারকে ফাইল পাঠানো
                await update.message.reply_text("✅ পাসওয়ার্ড সঠিক! ফাইল পাঠানো হচ্ছে...")
                
                if file_type == 'document': await context.bot.send_document(user_id, file_id)
                elif file_type == 'video': await context.bot.send_video(user_id, file_id)
                elif file_type == 'photo': await context.bot.send_photo(user_id, file_id)
                elif file_type == 'audio': await context.bot.send_audio(user_id, file_id)

                # ডাটাবেসে usage_count বাড়ানো
                new_usage = usage_count + 1
                c.execute("UPDATE files SET usage_count=? WHERE unique_code=?", (new_usage, unique_code))
                conn.commit()
                
                del context.user_data['attempting_code']
            else:
                await update.message.reply_text("❌ ভুল পাসওয়ার্ড।")
        else:
            await update.message.reply_text("❌ লিংকটি কাজ করছে না।")
        
        conn.close()
        return

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.ATTACHMENT | filters.PHOTO, handle_files_admin))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.run_polling()
