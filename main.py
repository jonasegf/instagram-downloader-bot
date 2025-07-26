import json
import logging
import os
import re
import threading
from datetime import datetime

import instaloader
import pytz
import requests
from dotenv import load_dotenv
from instaloader import Instaloader, Post, PostSidecarNode
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import CommandHandler, MessageHandler, filters, ApplicationBuilder, ContextTypes

TASHKENT_TZ = pytz.timezone("America/Sao_Paulo")  # Change to your desired timezone

# Load environment variables
load_dotenv()

# Logger setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Instagram credentials
USERNAME = os.getenv("INSTAGRAM_USERNAME")
PASSWORD = os.getenv("INSTAGRAM_PASSWORD")

# File paths
USERS_LOG_FILE = "users.log"
ADMIN_FILE = "admin.json"

# Instaloader setup
loader = Instaloader()

# Session file path
SESSION_FILE = f"{os.getcwd()}/session-{USERNAME}"

session_lock = threading.Lock()

def load_or_create_session():
    with session_lock:
        if os.path.exists(SESSION_FILE):
            loader.load_session_from_file(USERNAME, filename=SESSION_FILE)
        else:
            loader.login(USERNAME, PASSWORD)
            loader.save_session_to_file(SESSION_FILE)

# Admin-related functions
def get_admin():
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r") as file:
            return json.load(file).get("admin_id")
    return None

def set_admin(user_id):
    if not os.path.exists(ADMIN_FILE):  # Set admin only once
        with open(ADMIN_FILE, "w") as file:
            json.dump({"admin_id": user_id}, file)

# User logging function
def log_user_data(user):
    # Get the current time in the server's timezone and convert it to Tashkent time
    server_time = datetime.now()
    tashkent_time = server_time.astimezone(TASHKENT_TZ)

    user_data = {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "timestamp": tashkent_time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        # Read existing data
        if os.path.exists(USERS_LOG_FILE):
            with open(USERS_LOG_FILE, "r") as file:
                users = json.load(file)
        else:
            users = []

        # Update the timestamp if the user already exists
        for existing_user in users:
            if existing_user["user_id"] == user_data["user_id"]:
                existing_user["timestamp"] = user_data["timestamp"]
                break
        else:
            # Add new user if not found
            users.append(user_data)

        # Write updated data back to the file
        with open(USERS_LOG_FILE, "w") as file:
            json.dump(users, file, indent=4)

    except Exception as e:
        logger.error(f"Error logging user data: {e}")

# Command to list users and total counts
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_id = get_admin()

    if user.id != admin_id:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return

    try:
        if os.path.exists(USERS_LOG_FILE):
            with open(USERS_LOG_FILE, "r") as file:
                users = json.load(file)

            if not users:
                await update.message.reply_text("No users have used the bot yet.")
                return

            # Calculate total users and users who used the bot today
            total_users = len(users)
            today_users = sum(
                1 for u in users if datetime.strptime(u['timestamp'], "%Y-%m-%d %H:%M:%S").date() == datetime.now(TASHKENT_TZ).date()
            )

            # Preparing the response
            response = f"📊 Total users: {total_users}\n"
            response += f"🌍 Users who used today: {today_users}\n\n"
            response += "📋 List of users who used the bot:\n\n"
            for u in users:
                response += (
                    f"👤 User ID: {u['user_id']}\n"
                    f"   Username: @{u['username'] or 'N/A'}\n"
                    f"   First Name: {u['first_name']}\n"
                    f"   Last Active: {u['timestamp']}\n\n"
                )
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("No user log file found. No users have used the bot yet.")
    except Exception as e:
        logger.error(f"Error reading user log file: {e}")
        await update.message.reply_text("⚠️ An error occurred while retrieving user data.")

# Helper functions
def extract_shortcode(instagram_post):
    match = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?#&]+)", instagram_post)
    return match.group(1) if match else None

def is_valid_instagram_url(url):
    return bool(re.match(r"https?://(www\.)?instagram\.com/(p|reel|tv)/", url))

def fetch_instagram_data(instagram_post):
    shortcode = extract_shortcode(instagram_post)
    if not shortcode:
        return None

    try:
        post = Post.from_shortcode(loader.context, shortcode)
        return post.video_url if post.is_video else post.url
    except Exception as e:
        logger.error(f"Error fetching Instagram data: {e}")
        return None

# Command: Start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log_user_data(user)

    if get_admin() is None:
        set_admin(user.id)
        await update.message.reply_text("👑 You have been set as the admin!")

    if get_admin() == user.id:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return

    load_or_create_session()

    await update.message.reply_text(
        "👋 Welcome to the Instagram Saver Bot Fork!\n\n"
        "📩 Send me any **public** Instagram link (post, reel, or IGTV), and I'll fetch the media for you.\n"
        "⚠️ Make sure the post is **public** and not private.\n\n"
        "Happy downloading! 🎉"
    )

# Handle: Download with Threading
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if get_admin() == user.id:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return

    user = update.effective_user
    log_user_data(user)

    instagram_post = update.effective_message.text.strip()
    if not is_valid_instagram_url(instagram_post):
        await update.message.reply_text("❌ Invalid Instagram URL. Check the url.")
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    progress_message = await update.message.reply_text("⏳ Fetching media...")

    media_url = fetch_instagram_data(instagram_post)
    if not media_url:
        await progress_message.edit_text("❌ Failure fetching media. Check if the post is public or try again later.")
        return

    file_name = f"temp_{update.message.chat_id}.mp4" if "mp4" in media_url else f"temp_{update.message.chat_id}.jpg"
    try:
        response = requests.get(media_url, stream=True)
        response.raise_for_status()
        with open(file_name, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024):
                f.write(chunk)

        with open(file_name, "rb") as file:
            if "mp4" in file_name:
                await context.bot.send_video(chat_id=update.message.chat_id, video=file, caption="👾 Powered by @Instasave_downloader_bot")
            else:
                await context.bot.send_photo(chat_id=update.message.chat_id, photo=file, caption="👾 Powered by @Instasave_downloader_bot")

        await progress_message.delete()
    except Exception as e:
        logger.error(f"Failure when sending media: {e}")
        await progress_message.edit_text("❌ Failure when sending media, try again later.")
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)

# Main function
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download))

    application.run_polling()
    logger.info("Bot started and polling for updates...")

if __name__ == "__main__":
    main()
