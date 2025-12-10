import logging
import asyncio
import os
import uuid
import sys
from threading import Thread
from datetime import datetime
from flask import Flask
from pymongo import MongoClient
import certifi

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -------------------- CONFIG --------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8501752321:AAFmSLnhtO0jdlLyyrtPKdPFnL1nVPUkdDk")
SOURCE_CHAT_ID = -1003455503034   
ADMIN_IDS = [6872143322, 8363437161] 

# MongoDB URL (আপনার আগের দেওয়া URL টি বসালাম)
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://atkcyber5_db_user:adminabir221@cluster0.4iwef3e.mongodb.net/?appName=Cluster0")

# লগিং
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- SERVER (Keep Alive) ---
app = Flask('')
@app.route('/')
def home(): return "Bot Running with MongoDB"
def run_http(): 
    try: app.run(host='0.0.0.0', port=8080)
    except: pass
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

# -------------------- DATABASE ENGINE (MongoDB) --------------------

# মঙ্গোডিবি কানেকশন সেটআপ
try:
    # ca=certifi.where() ব্যবহার করা হয়েছে যাতে SSL এরর না দেয়
    client = MongoClient(MONGO_URL, tlsCAFile=certifi.where())
    db = client["FileShareBot_V2"] # ডাটাবেস নাম
    users_col = db["users"]        # ইউজার কালেকশন
    files_col = db["shared_files"] # ফাইল কালেকশন
    
    # কানেকশন চেক
    client.admin.command('ping')
    print("✅ Connected to MongoDB Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    sys.exit(1)

# -------------------- HANDLERS --------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def ensure_user(user):
    # ইউজার ডাটাবেসে আছে কিনা চেক, না থাকলে অ্যাড করবে (Upsert)
    try:
        users_col.update_one(
            {"user_id": user.id},
            {"$set": {
                "first_name": user.first_name,
                "username": user.username,
                "last_active": datetime.now()
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"User Save Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    
    args = context.args

    # --- লিংক থেকে আসলে ---
    if args and len(args) > 0:
        unique_code = args[0].strip()
        
        # ডাটাবেসে ফাইল খোঁজা
        file_data = files_col.find_one({"unique_code": unique_code})

        if not file_data:
            await update.message.reply_text("❌ <b>লিংকটি ভুল বা মেয়াদোত্তীর্ণ।</b>", parse_mode='HTML')
            return

        usage = file_data.get('usage_count', 0)
        limit = file_data.get('limit_count', 0)

        if usage >= limit:
            await update.message.reply_text("❌ <b>দুঃখিত! এই ফাইলের ডাউনলোড লিমিট শেষ।</b>", parse_mode='HTML')
            return

        context.user_data['attempting_code'] = unique_code
        remaining = limit - usage
        
        await update.message.reply_text(
            f"🔒 <b>ফাইল লক করা আছে!</b>\n\n"
            f"👇 পাসওয়ার্ড লিখুন:\n(বাকি: {remaining} জন)", 
            parse_mode='HTML'
        )

    # --- সাধারণ ওয়েলকাম ---
    else:
        if is_admin(user.id):
            await update.message.reply_text("👋 <b>Admin Panel</b>\nফাইল আপলোড করে `pass limit` লিখুন।", parse_mode='HTML')
        else:
            await update.message.reply_text(f"👋 হ্যালো {user.first_name}!")

# --- ফাইল আপলোড (Admin) ---
async def admin_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    msg = update.message
    file_id, file_type = None, None
    
    if msg.document: file_id, file_type = msg.document.file_id, 'document'
    elif msg.video: file_id, file_type = msg.video.file_id, 'video'
    elif msg.photo: file_id, file_type = msg.photo[-1].file_id, 'photo'
    elif msg.audio: file_id, file_type = msg.audio.file_id, 'audio'

    if file_id:
        # প্রাইভেট চ্যানেলে ব্যাকআপ রাখা
        try:
            forwarded = await context.bot.forward_message(chat_id=SOURCE_CHAT_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
            if forwarded.document: file_id = forwarded.document.file_id
            elif forwarded.video: file_id = forwarded.video.file_id
            elif forwarded.photo: file_id = forwarded.photo[-1].file_id
            elif forwarded.audio: file_id = forwarded.audio.file_id
        except Exception as e:
            print(f"⚠️ Backup Error: {e}")

        # টেম্পোরারি স্টোরেজ
        context.user_data['setup_file'] = {'file_id': file_id, 'file_type': file_type}
        await msg.reply_text("✅ ফাইল রিসিভড! এবার `pass limit` দিন (Example: `pass 50`)")

# --- পাসওয়ার্ড ও লিংক জেনারেশন ---
async def global_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # [ADMIN] লিংক তৈরি
    if is_admin(user_id) and 'setup_file' in context.user_data:
        try:
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ ভুল! লিখুন: `password 50`")
                return
            
            password = parts[0]
            limit_count = int(parts[1])
            file_data = context.user_data['setup_file']
            unique_code = str(uuid.uuid4())[:8]

            # MongoDB তে সেভ করা
            new_file = {
                "unique_code": unique_code,
                "file_id": file_data['file_id'],
                "file_type": file_data['file_type'],
                "password": password,
                "limit_count": limit_count,
                "usage_count": 0,
                "created_at": datetime.now()
            }
            files_col.insert_one(new_file)

            bot_user = await context.bot.get_me()
            link = f"https://t.me/{bot_user.username}?start={unique_code}"
            
            await update.message.reply_text(
                f"✅ **লিংক তৈরি হয়েছে!**\n\n"
                f"🔗 `{link}`\n"
                f"🔑 Pass: `{password}`\n"
                f"🔢 Limit: `{limit_count}`", 
                parse_mode='Markdown'
            )
            del context.user_data['setup_file']
            
        except ValueError:
            await update.message.reply_text("❌ লিমিট সংখ্যা হতে হবে (যেমন: 50)।")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # [USER] পাসওয়ার্ড চেক
    if 'attempting_code' in context.user_data:
        unique_code = context.user_data['attempting_code']
        
        # ডাটাবেস চেক
        file_data = files_col.find_one({"unique_code": unique_code})
        
        if not file_data:
            await update.message.reply_text("❌ ফাইলটি ডাটাবেসে পাওয়া যায়নি।")
            return
            
        if file_data['usage_count'] >= file_data['limit_count']:
             await update.message.reply_text("❌ লিমিট শেষ।")
             return

        if text == file_data['password']:
            await update.message.reply_text("✅ **সঠিক পাসওয়ার্ড!** ফাইল আপলোড হচ্ছে...", parse_mode='Markdown')
            try:
                ft = file_data['file_type']
                fid = file_data['file_id']
                
                # ফাইল সেন্ড করা
                if ft == 'document': await context.bot.send_document(user_id, fid)
                elif ft == 'video': await context.bot.send_video(user_id, fid)
                elif ft == 'photo': await context.bot.send_photo(user_id, fid)
                elif ft == 'audio': await context.bot.send_audio(user_id, fid)
                
                # Usage 1 বাড়ানো (MongoDB $inc অপারেটর)
                files_col.update_one(
                    {"unique_code": unique_code},
                    {"$inc": {"usage_count": 1}}
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Error sending file: {e}")
            
            del context.user_data['attempting_code']
        else:
            await update.message.reply_text("❌ ভুল পাসওয়ার্ড!")
        return

# -------------------- MAIN --------------------

def main():
    keep_alive()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # হ্যান্ডলার অ্যাড করা
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.VIDEO | filters.PHOTO | filters.AUDIO, admin_file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_handler))

    print("🚀 Bot Started Successfully with MongoDB!")
    app.run_polling()

if __name__ == '__main__':
    main()
