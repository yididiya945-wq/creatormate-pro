
import os
import logging
import threading
import requests

from flask import Flask, request, jsonify

# --------------------------------------------------
# CreatorMate Pro - Telegram AI Content Assistant
# --------------------------------------------------

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("creatormate")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

TELEGRAM_API = (
    f"https://api.telegram.org/bot{BOT_TOKEN}"
    if BOT_TOKEN else ""
)

# Temporary user selections. These reset when the service restarts.
pending_actions = {}
pending_lock = threading.Lock()

ACTIONS = {
    "caption": "Social media caption",
    "script": "Short-form video script",
    "hashtags": "Hashtag suggestions",
    "bio": "Professional profile bio",
    "planner": "Content plan",
}

SYSTEM_PROMPT = """
You are CreatorMate Pro, a professional AI content creation assistant.

Your job is to help users create original, useful, engaging content.

Available tasks:
- Social media captions
- TikTok, YouTube Shorts and Instagram Reels scripts
- Relevant hashtag suggestions
- Professional bios and descriptions
- Practical content calendars and plans

Rules:
1. Respond in the language the user uses, unless they request another language.
2. Make the result specific to the user's topic and target audience.
3. Use clear formatting and practical, ready-to-use content.
4. Do not invent facts, statistics, testimonials or guarantees.
5. Never promise viral results, followers or income.
6. If important details are missing, make reasonable assumptions
   and briefly state them, rather than asking many questions.
7. Keep the response useful and concise.
8. For video scripts, include a hook, main content and call to action.
9. For hashtags, provide relevant hashtags, not unrelated trending tags.
10. Follow the user's requested tone, platform and language.
"""

def telegram_request(method, payload, timeout=20):
    if not TELEGRAM_API:
        raise RuntimeError("Telegram bot token is not configured.")

    response = requests.post(
        f"{TELEGRAM_API}/{method}",
        json=payload,
        timeout=timeout
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {method}")

    return data.get("result")


def send_message(chat_id, text, reply_markup=None):
    # Telegram messages must not exceed 4096 characters.
    chunks = []
    while len(text) > 3900:
        split_at = text.rfind("\n", 0, 3900)
        if split_at < 1000:
            split_at = 3900
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    chunks.append(text)

    for index, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }

        # Attach the menu only to the final message.
        if index == len(chunks) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup

        telegram_request("sendMessage", payload)


def main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "✍️ Caption", "callback_data": "caption"},
                {"text": "🎬 Video Script", "callback_data": "script"},
            ],
            [
                {"text": "#️⃣ Hashtags", "callback_data": "hashtags"},
                {"text": "👤 Bio", "callback_data": "bio"},
            ],
            [
                {"text": "📅 Content Planner", "callback_data": "planner"},
            ],
            [
                {"text": "ℹ️ About", "callback_data": "about"},
                {"text": "❓ Help", "callback_data": "help"},
            ],
        ]
    }


def show_welcome(chat_id):
    text = (
        "Welcome to CreatorMate Pro! 🚀\n\n"
        "Your AI-powered content creation assistant.\n\n"
        "Choose a tool below to create content for your audience."
    )
    send_message(chat_id, text, main_menu())


def ask_ai(action, user_text):
    if not GROQ_API_KEY:
        raise RuntimeError("AI service is not configured.")

    task = ACTIONS.get(action, "Content creation")
    user_prompt = (
        f"Requested task: {task}\n"
        f"User's instructions and topic:\n{user_text}"
    )

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_completion_tokens": 1200,
            "temperature": 0.7,
        },
        timeout=60,
    )

    if response.status_code >= 400:
        logger.error(
            "Groq API returned HTTP %s",
            response.status_code
        )
        response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])

    if not choices:
        raise RuntimeError("The AI returned no answer.")

    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("The AI returned an empty answer.")

    return content.strip()


def answer_callback(callback):
    callback_id = callback.get("id")
    data = callback.get("data", "")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    try:
        telegram_request(
            "answerCallbackQuery",
            {"callback_query_id": callback_id},
        )
    except Exception:
        logger.warning("Could not acknowledge callback.")

    if not chat_id:
        return

    if data == "about":
        send_message(
            chat_id,
            "CreatorMate Pro helps you create captions, video scripts, "
            "hashtags, professional bios and content plans with AI.",
            main_menu(),
        )
        return

    if data == "help":
        send_message(
            chat_id,
            "How to use CreatorMate Pro:\n\n"
            "1. Choose a tool from the menu.\n"
            "2. Send your topic, platform and audience.\n"
            "3. Wait for your AI-generated content.\n\n"
            "Use /start to open the menu again.",
            main_menu(),
        )
        return

    if data in ACTIONS:
        with pending_lock:
            pending_actions[chat_id] = data

        prompts = {
            "caption": "Send your topic, platform and preferred tone.",
            "script": "Send your video topic, platform and desired length.",
            "hashtags": "Send your topic, platform and target audience.",
            "bio": "Send your role, niche and the platform for your bio.",
            "planner": "Send your niche, platform and planning period.",
        }

        send_message(
            chat_id,
            f"✨ {ACTIONS[data]}\n\n{prompts[data]}\n\n"
            "Send your details in one message.",
            {"inline_keyboard": [[
                {"text": "🏠 Main Menu", "callback_data": "menu"}
            ]]},
        )
        return

    if data == "menu":
        show_welcome(chat_id)
        return

    send_message(chat_id, "Please choose an option from the menu.", main_menu())


def handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id:
        return

    if not isinstance(text, str) or not text.strip():
        send_message(
            chat_id,
            "Please send a text message describing the content you need.",
            main_menu(),
        )
        return

    text = text.strip()

    if text.startswith("/start") or text.startswith("/menu"):
        with pending_lock:
            pending_actions.pop(chat_id, None)
        show_welcome(chat_id)
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            "Choose a tool from the menu, then send your topic and "
            "requirements. Use /start to return to the main menu.",
            main_menu(),
        )
        return

    if text.startswith("/about"):
        send_message(
            chat_id,
            "CreatorMate Pro is an AI assistant for social media content "
            "creation, video scripts, hashtags, bios and content planning.",
            main_menu(),
        )
        return

    with pending_lock:
        action = pending_actions.pop(chat_id, None)

    if not action:
        send_message(
            chat_id,
            "Choose a content tool first, then send your topic.",
            main_menu(),
        )
        return

    send_message(chat_id, "⏳ Creating your content. Please wait...")

    try:
        result = ask_ai(action, text)
        send_message(chat_id, result, main_menu())
    except requests.Timeout:
        logger.warning("AI request timed out.")
        send_message(
            chat_id,
            "The AI service took too long to respond. Please try again.",
            main_menu(),
        )
    except requests.RequestException:
        logger.exception("External API request failed.")
        send_message(
            chat_id,
            "The service is temporarily unavailable. Please try again later.",
            main_menu(),
        )
    except Exception:
        logger.exception("Content generation failed.")
        send_message(
            chat_id,
            "Something went wrong while creating your content. "
            "Please try again later.",
            main_menu(),
        )


@app.get("/")
def health():
    return jsonify({
        "service": "CreatorMate Pro",
        "status": "running",
    }), 200


@app.post("/telegram")
def telegram_webhook():
    if not BOT_TOKEN:
        return jsonify({"error": "Bot is not configured"}), 503

    if WEBHOOK_SECRET:
        supplied_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )
        if supplied_secret != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify({"ok": True}), 200

    try:
        if "callback_query" in update:
            answer_callback(update["callback_query"])
        elif "message" in update:
            handle_message(update["message"])
    except Exception:
        logger.exception("Failed to process Telegram update.")

    return jsonify({"ok": True}), 200


def configure_webhook():
    if not BOT_TOKEN or not RENDER_URL:
        logger.info(
            "Webhook setup skipped: bot token or public service URL missing."
        )
        return

    payload = {
        "url": f"{RENDER_URL}/telegram",
        "allowed_updates": ["message", "callback_query"],
    }

    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    try:
        result = telegram_request("setWebhook", payload)
        logger.info("Telegram webhook configured: %s", bool(result))
    except Exception:
        logger.exception(
            "Automatic webhook setup failed. Check Render environment "
            "variables and deployment logs."
        )


# Configure the webhook when the application starts.
configure_webhook()

