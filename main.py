import logging
import asyncio
import sqlite3
import os
import uuid
import sys
from threading import Thread
from datetime import datetime
from flask import Flask

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

# ডাটাবেস পাথ সেটআপ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'bot_data.db')

# লগিং
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot Running"
def run_http(): 
    try: app.run(host='0.0.0.0', port=8080)
    except: pass
def keep_alive(): 
    t = Thread(target=run_http)
    t.start()

# -------------------- DATABASE ENGINE --------------------

def db_query(query, params=(), fetchone=False, commit=False):
    con = None
    try:
        con = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=20)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(query, params)
        if commit: con.commit()
        result = cur.fetchone() if fetchone else cur.fetchall()
        return result
    except sqlite3.Error as e:
        # সাধারণ এরর প্রিন্ট করবে, কিন্তু প্রোগ্রাম বন্ধ হবে না
        logger.error(f"DB Error: {e}")
        return None
    finally:
        if con: con.close()

def setup_database():
    print(f"📂 Database Path: {DB_FILE}")
    
    # 1. টেবিল তৈরি (যদি না থাকে)
    db_query("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            join_date TEXT
        )""", commit=True)

    db_query('''
        CREATE TABLE IF NOT EXISTS shared_files (
            unique_code TEXT PRIMARY KEY,
            file_id TEXT,
            file_type TEXT,
            password TEXT,
            limit_count INTEGER,
            usage_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    ''', commit=True)
    
    # --- AUTO FIX FOR OLD DATABASE ---
    # যদি আগের ডাটাবেস থাকে এবং created_at না থাকে, তবে এটি অ্যাড করবে
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        try:
            cur.execute("ALTER TABLE shared_files ADD COLUMN created_at TEXT")
            con.commit()
            print("✅ Database Fixed: Added missing 'created_at' column.")
        except sqlite3.OperationalError:
            # কলাম ইতিমধ্যে থাকলে এই এরর আসবে, আমরা ইগনোর করব
            pass
        con.close()
    except Exception as e:
        print(f"⚠️ Migration Check Error: {e}")

    print("✅ Database Ready!")

# -------------------- HANDLERS --------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def ensure_user(user):
    try:
        db_query("INSERT OR IGNORE INTO users (user_id, first_name, username, join_date) VALUES (?, ?, ?, ?)",
                 (user.id, user.first_name, user.username, str(datetime.now())), commit=True)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    
    args = context.args

    # --- লিংক থেকে আসলে ---
    if args and len(args) > 0:
        unique_code = args[0].strip()
        
        # ডাটাবেসে খোঁজা
        result = db_query("SELECT * FROM shared_files WHERE unique_code=?", (unique_code,), fetchone=True)

        if not result:
            await update.message.reply_text("❌ <b>লিংকটি ভুল বা মেয়াদোত্তীর্ণ।</b>", parse_mode='HTML')
            return

        if result['usage_count'] >= result['limit_count']:
            await update.message.reply_text("❌ <b>দুঃখিত! এই ফাইলের ডাউনলোড লিমিট শেষ।</b>", parse_mode='HTML')
            return

        context.user_data['attempting_code'] = unique_code
        await update.message.reply_text(
            f"🔒 <b>ফাইল লক করা আছে!</b>\n\n"
            f"👇 পাসওয়ার্ড লিখুন:\n(বাকি: {result['limit_count'] - result['usage_count']} জন)", 
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
        try:
            forwarded = await context.bot.forward_message(chat_id=SOURCE_CHAT_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
            if forwarded.document: file_id = forwarded.document.file_id
            elif forwarded.video: file_id = forwarded.video.file_id
            elif forwarded.photo: file_id = forwarded.photo[-1].file_id
            elif forwarded.audio: file_id = forwarded.audio.file_id
        except Exception as e:
            print(f"⚠️ Backup Error: {e}")

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

            # সেভ করা
            db_query("""
                INSERT INTO shared_files (unique_code, file_id, file_type, password, limit_count, usage_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (unique_code, file_data['file_id'], file_data['file_type'], password, limit_count, 0, str(datetime.now())), commit=True)

            bot_user = await context.bot.get_me()
            link = f"https://t.me/{bot_user.username}?start={unique_code}"
            
            await update.message.reply_text(
                f"✅ **লিংক তৈরি হয়েছে!**\n\n"
                f"🔗 `{link}`\n"
                f"🔑 Pass: `{password}`", 
                parse_mode='Markdown'
            )
            del context.user_data['setup_file']
            
        except ValueError:
            await update.message.reply_text("❌ লিমিট সংখ্যা হতে হবে।")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # [USER] পাসওয়ার্ড চেক
    if 'attempting_code' in context.user_data:
        unique_code = context.user_data['attempting_code']
        result = db_query("SELECT * FROM shared_files WHERE unique_code=?", (unique_code,), fetchone=True)
        
        if not result:
            await update.message.reply_text("❌ ফাইল পাওয়া যায়নি।")
            return
            
        if result['usage_count'] >= result['limit_count']:
             await update.message.reply_text("❌ লিমিট শেষ।")
             return

        if text == result['password']:
            await update.message.reply_text("✅ **সঠিক পাসওয়ার্ড!** ফাইল আপলোড হচ্ছে...", parse_mode='Markdown')
            try:
                ft = result['file_type']
                fid = result['file_id']
                if ft == 'document': await context.bot.send_document(user_id, fid)
                elif ft == 'video': await context.bot.send_video(user_id, fid)
                elif ft == 'photo': await context.bot.send_photo(user_id, fid)
                elif ft == 'audio': await context.bot.send_audio(user_id, fid)
                
                db_query("UPDATE shared_files SET usage_count=usage_count+1 WHERE unique_code=?", (unique_code,), commit=True)
            except Exception as e:
                await update.message.reply_text(f"❌ Error sending file: {e}")
            
            del context.user_data['attempting_code']
        else:
            await update.message.reply_text("❌ ভুল পাসওয়ার্ড!")
        return

# -------------------- MAIN --------------------

def main():
    setup_database()
    keep_alive()
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.VIDEO | filters.PHOTO | filters.AUDIO, admin_file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_handler))

    print("🚀 Bot Started Successfully!")
    app.run_polling()

if __name__ == '__main__':
    main()
