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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8450069015:AAGb9DnEP4RmBJS5Q1EQ0S1S2mgc5q24-KI")
SOURCE_CHAT_ID = -1003455503034   

# ✅ এডমিন আইডি লিস্ট
ADMIN_IDS = [6872143322, 8363437161, 6698901002] 

# MongoDB URL
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://atkcyber5_db_user:adminabir221@cluster0.4iwef3e.mongodb.net/?appName=Cluster0")

# লগিং
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- SERVER (Keep Alive) ---
app = Flask('')
@app.route('/')
def home(): return "Bot Running with Auto Delete Feature"
def run_http(): 
    try: app.run(host='0.0.0.0', port=8080)
    except: pass
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

# -------------------- DATABASE ENGINE (MongoDB) --------------------
try:
    client = MongoClient(MONGO_URL, tlsCAFile=certifi.where())
    db = client["FileShareBot_V2"] 
    users_col = db["users"]        
    files_col = db["shared_files"] 
    
    client.admin.command('ping')
    print("✅ Connected to MongoDB Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    sys.exit(1)

# -------------------- HANDLERS --------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def ensure_user(user):
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

# --- অটো ডিলিট ফাংশন (Job Queue) ---
async def delete_file_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    channel_msg_id = job_data.get('channel_msg_id')
    unique_code = job_data.get('unique_code')

    try:
        # চ্যানেল থেকে মেসেজ ডিলিট করা
        await context.bot.delete_message(chat_id=SOURCE_CHAT_ID, message_id=channel_msg_id)
        print(f"🗑️ File deleted from channel. Msg ID: {channel_msg_id}")
        
        # ডাটাবেসে আপডেট করা
        files_col.update_one(
            {"unique_code": unique_code},
            {"$set": {"is_deleted_from_channel": True}}
        )
    except Exception as e:
        print(f"⚠️ Auto Delete Failed (Message might be already deleted): {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    
    args = context.args

    # --- লিংক থেকে আসলে ---
    if args and len(args) > 0:
        unique_code = args[0].strip()
        
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
            await update.message.reply_text(
                f"👋 <b>Welcome Admin!</b>\n"
                f"✅ Your ID: `{user.id}`\n\n"
                f"ফাইল আপলোড করুন, তারপর পাসওয়ার্ড, লিমিট এবং সময় সেট করুন।", 
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(f"👋 হ্যালো {user.first_name}!")

# --- ফাইল আপলোড (Admin Only) ---
async def admin_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id): return

    msg = update.message
    file_id, file_type = None, None
    
    if msg.document: file_id, file_type = msg.document.file_id, 'document'
    elif msg.video: file_id, file_type = msg.video.file_id, 'video'
    elif msg.photo: file_id, file_type = msg.photo[-1].file_id, 'photo'
    elif msg.audio: file_id, file_type = msg.audio.file_id, 'audio'

    if file_id:
        channel_msg_id = None
        try:
            # চ্যানেলে ফরোয়ার্ড করা হচ্ছে
            forwarded = await context.bot.forward_message(chat_id=SOURCE_CHAT_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
            channel_msg_id = forwarded.message_id
            
            if forwarded.document: file_id = forwarded.document.file_id
            elif forwarded.video: file_id = forwarded.video.file_id
            elif forwarded.photo: file_id = forwarded.photo[-1].file_id
            elif forwarded.audio: file_id = forwarded.audio.file_id
        except Exception as e:
            print(f"⚠️ Backup Error: {e}")

        context.user_data['setup_file'] = {
            'file_id': file_id, 
            'file_type': file_type, 
            'channel_msg_id': channel_msg_id,
            'step': 1 
        }
        await msg.reply_text("✅ ফাইল রিসিভ হয়েছে!\n\n**Step 1:** পাসওয়ার্ড এবং লিমিট লিখুন।\nFormat: `password limit`\nExample: `pass123 20`", parse_mode='Markdown')

# --- পাসওয়ার্ড, লিংক এবং টাইমার সেটআপ ---
async def global_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # [ADMIN] সেটআপ হ্যান্ডলার
    if is_admin(user_id) and 'setup_file' in context.user_data:
        setup_data = context.user_data['setup_file']
        
        # Step 1: পাসওয়ার্ড এবং লিমিট নেওয়া
        if setup_data['step'] == 1:
            try:
                parts = text.split()
                if len(parts) < 2:
                    await update.message.reply_text("❌ ভুল! লিখুন: `password limit` (Ex: `pass 10`)", parse_mode='Markdown')
                    return
                
                setup_data['password'] = parts[0]
                setup_data['limit_count'] = int(parts[1])
                setup_data['step'] = 2 
                
                await update.message.reply_text(
                    "✅ পাসওয়ার্ড ও লিমিট সেট হয়েছে।\n\n"
                    "**Step 2:** এই ফাইলটি **Private Channel** এ কতক্ষণ থাকবে? (মিনিটে লিখুন)\n"
                    "উদাহরণ: `20` (মানে ২০ মিনিট পর ডিলিট হবে)",
                    parse_mode='Markdown'
                )
            except ValueError:
                await update.message.reply_text("❌ লিমিট সংখ্যা হতে হবে (যেমন: 50)।")
            return

        # Step 2: সময় নেওয়া এবং লিংক জেনারেট করা
        elif setup_data['step'] == 2:
            try:
                delete_minutes = int(text)
                delete_seconds = delete_minutes * 60
                
                unique_code = str(uuid.uuid4())[:8]
                
                new_file = {
                    "unique_code": unique_code,
                    "file_id": setup_data['file_id'],
                    "file_type": setup_data['file_type'],
                    "password": setup_data['password'],
                    "limit_count": setup_data['limit_count'],
                    "usage_count": 0,
                    "created_at": datetime.now(),
                    "delete_in_mins": delete_minutes
                }
                files_col.insert_one(new_file)

                # Job Queue তে ডিলিট শিডিউল করা
                if context.job_queue:
                    if setup_data['channel_msg_id']:
                        context.job_queue.run_once(
                            delete_file_job, 
                            delete_seconds, 
                            data={
                                'channel_msg_id': setup_data['channel_msg_id'],
                                'unique_code': unique_code
                            }
                        )
                else:
                    await update.message.reply_text("⚠️ **সতর্কতা:** JobQueue সক্রিয় নেই। অটো ডিলিট কাজ করবে না। requirements.txt চেক করুন।")

                bot_user = await context.bot.get_me()
                link = f"https://t.me/{bot_user.username}?start={unique_code}"
                
                await update.message.reply_text(
                    f"✅ **কাজ সম্পন্ন!**\n\n"
                    f"🔗 Link: `{link}`\n"
                    f"🔑 Pass: `{setup_data['password']}`\n"
                    f"🔢 Limit: `{setup_data['limit_count']}`\n"
                    f"⏳ Auto Delete: `{delete_minutes} mins`", 
                    parse_mode='Markdown'
                )
                del context.user_data['setup_file']
                
            except ValueError:
                await update.message.reply_text("❌ দয়া করে শুধু সংখ্যা লিখুন (মিনিট)। যেমন: 20")
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {e}")
            return

    # [USER] পাসওয়ার্ড চেক
    if 'attempting_code' in context.user_data:
        unique_code = context.user_data['attempting_code']
        
        file_data = files_col.find_one({"unique_code": unique_code})
        
        if not file_data:
            await update.message.reply_text("❌ ফাইল ডাটাবেসে নেই বা ডিলিট হয়েছে।")
            return
            
        if file_data['usage_count'] >= file_data['limit_count']:
             await update.message.reply_text("❌ লিমিট শেষ।")
             return

        if text == file_data['password']:
            await update.message.reply_text("✅ **সঠিক পাসওয়ার্ড!** ফাইল আপলোড হচ্ছে...", parse_mode='Markdown')
            try:
                ft = file_data['file_type']
                fid = file_data['file_id']
                
                if ft == 'document': await context.bot.send_document(user_id, fid)
                elif ft == 'video': await context.bot.send_video(user_id, fid)
                elif ft == 'photo': await context.bot.send_photo(user_id, fid)
                elif ft == 'audio': await context.bot.send_audio(user_id, fid)
                
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
    
    # Application Builder
    app = Application.builder().token(BOT_TOKEN).build()

    # Check if JobQueue is available
    if app.job_queue is None:
        print("❌ ERROR: JobQueue is NOT available. Please install 'python-telegram-bot[job-queue]'.")
    else:
        print("✅ JobQueue is active.")
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.VIDEO | filters.PHOTO | filters.AUDIO, admin_file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_handler))

    print("🚀 Bot Started Successfully with Auto-Delete!")
    app.run_polling()

if __name__ == '__main__':
    main()
