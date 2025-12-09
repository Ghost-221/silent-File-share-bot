import logging
import sqlite3
import uuid
import os
import asyncio
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- ১. ফ্লাস্ক সার্ভার (রেন্ডারে বট সজাগ রাখার জন্য) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running successfully!"

def run_http():
    try:
        port = int(os.environ.get("PORT", 8080))
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        print(f"Server Error: {e}")

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# --- ২. কনফিগারেশন ---
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# এডমিন আইডি সেটআপ (কমা দিয়ে একাধিক আইডি দেওয়া যাবে)
admin_env = os.environ.get("ADMIN_IDS", "123456789") 
ADMIN_IDS = [int(x.strip()) for x in admin_env.split(',') if x.strip().isdigit()]

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

    # --- ক) ইউজার লিংকে ক্লিক করলে ---
    if args:
        unique_code = args[0]
        
        # ১. লোডিং এনিমেশন শুরু
        loading_msg = await update.message.reply_text("⏳ <b>সার্ভার থেকে ফাইল লোড হচ্ছে...</b>", parse_mode='HTML')
        await asyncio.sleep(1.5) # ১.৫ সেকেন্ড লোডিং দেখাবে
        
        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        conn.close()

        if result:
            limit_count, usage_count = result
            
            # লিমিট চেক
            if usage_count >= limit_count:
                 await loading_msg.edit_text("❌ <b>দুঃখিত! এই ফাইলের ডাউনলোড লিমিট শেষ হয়ে গেছে।</b>", parse_mode='HTML')
            else:
                # সেশন সেভ করা হচ্ছে
                context.user_data['attempting_code'] = unique_code
                
                # মেসেজ এডিট করে পাসওয়ার্ড চাওয়া
                await loading_msg.edit_text(
                    f"🔒 <b>ফাইলটি লক করা আছে!</b>\n"
                    f"👇 ফাইলটি পেতে নিচে পাসওয়ার্ড লিখুন:\n"
                    f"(বাকি আছে: {limit_count - usage_count} জন)", 
                    parse_mode='HTML'
                )
        else:
            await loading_msg.edit_text("❌ <b>লিংকটি ভুল বা মেয়াদোত্তীর্ণ।</b>", parse_mode='HTML')

    # --- খ) শুধু /start দিলে (এডমিন চেক) ---
    else:
        if user_id in ADMIN_IDS:
            await update.message.reply_text(
                f"স্বাগতম এডমিন (ID: {user_id})! 👑\n\n"
                "📂 <b>নিয়মাবলী:</b>\n"
                "১. যেকোনো ফাইল, ভিডিও বা অডিও এখানে ফরোয়ার্ড বা আপলোড করুন।\n"
                "২. এরপর পাসওয়ার্ড এবং লিমিট সেট করুন।",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("👋 হ্যালো! ফাইল পেতে হলে সঠিক লিংক ব্যবহার করুন।")

async def handle_files_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # শুধুমাত্র এডমিন ফাইল আপলোড করতে পারবে
    if user_id not in ADMIN_IDS:
        return 

    file_id, file_type = None, None

    # ফাইলের ধরন শনাক্ত করা
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
        # ফাইল আইডি মেমোরিতে রাখা
        context.user_data['uploading_file_id'] = file_id
        context.user_data['uploading_file_type'] = file_type
        
        await update.message.reply_text(
            "✅ <b>ফাইল রিসিভ হয়েছে!</b>\n\n"
            "এখন পাসওয়ার্ড এবং লিমিট সেট করুন।\n"
            "📝 ফরম্যাট: `পাসওয়ার্ড` `লিমিট`\n"
            "উদাহরণ: `movi123 50`",
            parse_mode='HTML'
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # --- ১. এডমিন পাসওয়ার্ড সেট করছে ---
    if user_id in ADMIN_IDS and 'uploading_file_id' in context.user_data:
        try:
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ ভুল ফরম্যাট! লিখুন: `pass` `limit` (যেমন: `abc 10`)")
                return
            
            password = parts[0]
            limit_count = int(parts[1])

            file_id = context.user_data['uploading_file_id']
            file_type = context.user_data['uploading_file_type']
            unique_code = str(uuid.uuid4())[:8] # ইউনিক কোড জেনারেট

            conn = sqlite3.connect('files.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("INSERT INTO files (unique_code, file_id, file_type, password, limit_count, usage_count) VALUES (?, ?, ?, ?, ?, ?)", 
                      (unique_code, file_id, file_type, password, limit_count, 0))
            conn.commit()
            conn.close()

            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={unique_code}"
            
            await update.message.reply_text(
                f"✅ **নতুন লিংক তৈরি হয়েছে!**\n\n"
                f"🔑 পাসওয়ার্ড: `{password}`\n"
                f"👥 ডাউনলোড লিমিট: {limit_count} জন\n"
                f"🔗 লিংক: {link}\n\n"
                f"(কপি করতে লিংকে ক্লিক করুন)", 
                parse_mode='Markdown'
            )
            # মেমোরি ক্লিয়ার
            del context.user_data['uploading_file_id']
        
        except ValueError:
            await update.message.reply_text("❌ লিমিট অবশ্যই ইংরেজি সংখ্যা হতে হবে।")
        return
    
    # এডমিন যদি ফাইল ছাড়া টেক্সট দেয় (সতর্কবার্তা)
    elif user_id in ADMIN_IDS and not 'attempting_code' in context.user_data:
         # এটি তখন কাজ করবে যদি এডমিন পাসওয়ার্ড সেট করতে চায় কিন্তু ফাইল আপলোড করেনি
         # তবে সাধারণ চ্যাটিং আটকাতে চাইলে এই অংশ বাদ দিতে পারেন
         pass 

    # --- ২. ইউজার পাসওয়ার্ড দিচ্ছে ---
    if 'attempting_code' in context.user_data:
        user_pass = text
        unique_code = context.user_data['attempting_code']
        
        # ২. যাচাইকরণ এনিমেশন
        status_msg = await update.message.reply_text("🔄 <b>পাসওয়ার্ড যাচাই করা হচ্ছে...</b>", parse_mode='HTML')
        await asyncio.sleep(1) # ১ সেকেন্ড ওয়েট

        conn = sqlite3.connect('files.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT file_id, file_type, password, limit_count, usage_count FROM files WHERE unique_code=?", (unique_code,))
        result = c.fetchone()
        
        if result:
            file_id, file_type, db_pass, limit_count, usage_count = result
            
            # আবার লিমিট চেক (যদি ইতিমধ্যে শেষ হয়ে যায়)
            if usage_count >= limit_count:
                await status_msg.edit_text("❌ দুঃখিত, লিমিট শেষ হয়ে গেছে।")
                conn.close()
                del context.user_data['attempting_code']
                return

            # পাসওয়ার্ড চেকিং
            if user_pass == db_pass:
                await status_msg.edit_text("✅ <b>সঠিক পাসওয়ার্ড! ফাইল পাঠানো হচ্ছে... 📤</b>", parse_mode='HTML')
                await asyncio.sleep(0.5)
                
                # ফাইল পাঠানো (এখানে কোনো ডিলিট টাইমার নেই, তাই ফাইল পার্মানেন্ট থাকবে)
                try:
                    if file_type == 'document': await context.bot.send_document(user_id, file_id, caption="✅ এই নিন আপনার ফাইল।")
                    elif file_type == 'video': await context.bot.send_video(user_id, file_id, caption="✅ এই নিন আপনার ভিডিও।")
                    elif file_type == 'photo': await context.bot.send_photo(user_id, file_id, caption="✅ এই নিন আপনার ছবি।")
                    elif file_type == 'audio': await context.bot.send_audio(user_id, file_id, caption="✅ এই নিন আপনার অডিও।")
                except Exception as e:
                    await update.message.reply_text("❌ ফাইল পাঠাতে সমস্যা হয়েছে। সম্ভবত ফাইলটি সার্ভার থেকে ডিলিট হয়ে গেছে।")

                # ব্যবহার সংখ্যা আপডেট
                new_usage = usage_count + 1
                c.execute("UPDATE files SET usage_count=? WHERE unique_code=?", (new_usage, unique_code))
                conn.commit()
                
                # সেশন শেষ
                del context.user_data['attempting_code']
            else:
                await status_msg.edit_text("❌ <b>ভুল পাসওয়ার্ড!</b> দয়া করে আবার চেষ্টা করুন।", parse_mode='HTML')
        else:
            await status_msg.edit_text("❌ লিংকটি আর কার্যকর নয়।")
        
        conn.close()
        return

if __name__ == '__main__':
    keep_alive()
    application = ApplicationBuilder().token(TOKEN).build()
    
    # হ্যান্ডলার যোগ করা
    application.add_handler(CommandHandler('start', start))
    # এডমিনের ফাইল রিসিভ করার জন্য
    application.add_handler(MessageHandler(filters.ATTACHMENT | filters.PHOTO | filters.VIDEO | filters.AUDIO, handle_files_admin))
    # টেক্সট (পাসওয়ার্ড সেট বা পাসওয়ার্ড চেক) হ্যান্ডল করার জন্য
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    
    print("Bot is polling...")
    application.run_polling()
