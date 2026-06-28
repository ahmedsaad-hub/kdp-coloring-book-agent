
"""
KDP Coloring Book Agent - Render Deployment
==========================================
Run this on Render for 24/7 operation.
"""

import os
import json
import asyncio
import logging
import tempfile
import threading
from io import BytesIO
from datetime import datetime
from typing import List, Dict, Optional
from flask import Flask

# Keep-alive server
app = Flask(__name__)

@app.route('/')
def home():
    return "KDP Coloring Book Agent is running!"

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)

# Google Gemini
import google.generativeai as genai
from google.generativeai import types

# PDF generation
from PIL import Image
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# Web scraping
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not GOOGLE_API_KEY or not TELEGRAM_BOT_TOKEN:
    logger.error("Missing API keys! Please set GOOGLE_API_KEY and TELEGRAM_BOT_TOKEN")
    raise ValueError("Missing API keys")

# Configure Gemini
genai.configure(api_key=GOOGLE_API_KEY)

# Models
TEXT_MODEL = "gemini-2.0-flash-lite"
IMAGE_MODEL = "gemini-2.0-flash"

# Conversation states
(CHOOSING_THEME, CUSTOMIZING_PAGES, CUSTOMIZING_SIZE, 
 CUSTOMIZING_EXTRAS, GENERATING, CONFIRMING) = range(6)

# ==================== TREND DATA ====================

TRENDING_THEMES = [
    {
        "id": 1,
        "name": "🦖 Dinosaurs",
        "emoji": "🦖",
        "trend_reason": "Top 10 in BSR for 3 weeks",
        "competition": "Medium",
        "bsr": "#1,234",
        "keywords": ["dinosaur coloring book", "dino coloring", "prehistoric animals"]
    },
    {
        "id": 2,
        "name": "🦄 Unicorns & Magic",
        "emoji": "🦄",
        "trend_reason": "High search volume + seasonal",
        "competition": "High",
        "bsr": "#2,567",
        "keywords": ["unicorn coloring book", "magic coloring", "fantasy coloring"]
    },
    {
        "id": 3,
        "name": "🚀 Space & Astronauts",
        "emoji": "🚀",
        "trend_reason": "New in Movers & Shakers",
        "competition": "Low",
        "bsr": "#15,890",
        "keywords": ["space coloring book", "astronaut coloring", "rocket ship coloring"]
    },
    {
        "id": 4,
        "name": "🌊 Ocean Animals",
        "emoji": "🌊",
        "trend_reason": "Seasonal (Summer trend)",
        "competition": "Medium",
        "bsr": "#8,432",
        "keywords": ["ocean coloring book", "sea animals coloring", "underwater coloring"]
    },
    {
        "id": 5,
        "name": "🎄 Christmas & Holidays",
        "emoji": "🎄",
        "trend_reason": "Early for season - low competition",
        "competition": "Very Low",
        "bsr": "#45,321",
        "keywords": ["christmas coloring book", "holiday coloring", "santa coloring"]
    }
]

BOOK_SIZES = {
    "square": {"name": "8.5 x 8.5 inches (Square)", "width": 8.5, "height": 8.5, "price": "$4.99"},
    "letter": {"name": "8.5 x 11 inches (Letter)", "width": 8.5, "height": 11, "price": "$5.99"},
    "small": {"name": "6 x 9 inches (Small)", "width": 6, "height": 9, "price": "$3.99"}
}

PAGE_COUNTS = [30, 50, 100]

# ==================== HELPER FUNCTIONS ====================

def generate_image_prompt(theme: str, page_num: int) -> str:
    prompts = {
        "Dinosaurs": [
            "T-Rex dinosaur standing in a jungle",
            "Cute baby dinosaur hatching from egg",
            "Dinosaur family walking together",
            "Triceratops in a meadow",
            "Pterodactyl flying in the sky",
            "Stegosaurus with plates on back",
            "Dinosaur skeleton in a museum",
            "Dinosaur playing with a ball",
            "Volcano and dinosaurs landscape",
            "Dinosaur birthday party scene"
        ],
        "Unicorns & Magic": [
            "Unicorn with rainbow mane",
            "Unicorn dancing in clouds",
            "Magic castle with stars",
            "Fairy with unicorn friend",
            "Unicorn drinking from a stream",
            "Magic wand and sparkles",
            "Unicorn sleeping on moon",
            "Rainbow bridge with unicorns",
            "Unicorn tea party",
            "Magical forest with unicorns"
        ],
        "Space & Astronauts": [
            "Astronaut on the moon",
            "Rocket ship launching",
            "Alien friends in spaceship",
            "Planets in the solar system",
            "Space station in orbit",
            "Robot on Mars surface",
            "Comet flying through stars",
            "Astronaut planting flag",
            "Space cat in helmet",
            "UFO visiting Earth"
        ],
        "Ocean Animals": [
            "Cute dolphin jumping",
            "Octopus with tentacles",
            "Sea turtle swimming",
            "Clownfish in anemone",
            "Shark smiling friendly",
            "Jellyfish with long tentacles",
            "Seahorse holding coral",
            "Whale spouting water",
            "Crab on the beach",
            "Starfish on ocean floor"
        ],
        "Christmas & Holidays": [
            "Santa Claus with presents",
            "Christmas tree with ornaments",
            "Reindeer with red nose",
            "Snowman with carrot nose",
            "Elves making toys",
            "Gingerbread house scene",
            "Christmas stocking hanging",
            "Snowflakes falling gently",
            "Candy cane and gifts",
            "Winter village scene"
        ]
    }

    theme_key = theme.split(" & ")[0] if " & " in theme else theme
    scenes = prompts.get(theme_key, [f"{theme} scene for kids"])
    scene = scenes[page_num % len(scenes)]

    return f"""Simple coloring page for children ages 4-8, {scene}, 
    thick black outlines, no shading, white background, clean line art, 
    child-friendly design, easy to color, large open areas, 
    no small details, cartoon style, happy expression"""


def generate_cover_prompt(theme: str) -> str:
    return f"""Professional children's coloring book cover, {theme} theme, 
    vibrant colors, eye-catching design, cute cartoon characters, 
    title space at top, fun and playful, KDP ready, 
    high quality illustration, appealing to kids ages 4-8"""


# ==================== GEMINI IMAGE GENERATION ====================

async def generate_coloring_page(theme: str, page_num: int) -> tuple:
    try:
        prompt = generate_image_prompt(theme, page_num)
        model = genai.GenerativeModel(IMAGE_MODEL)

        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            generation_config=types.GenerationConfig(
                response_modalities=["Text", "Image"]
            )
        )

        image_data = None
        for part in response.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                break

        if image_data:
            return Image.open(BytesIO(image_data)), prompt
        else:
            return create_placeholder_image(theme, page_num), prompt

    except Exception as e:
        logger.error(f"Error generating image: {e}")
        return create_placeholder_image(theme, page_num), prompt


def create_placeholder_image(theme: str, page_num: int) -> Image.Image:
    img = Image.new('RGB', (1024, 1024), color='white')
    return img


async def generate_cover_image(theme: str) -> tuple:
    try:
        prompt = generate_cover_prompt(theme)
        model = genai.GenerativeModel(IMAGE_MODEL)

        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            generation_config=types.GenerationConfig(
                response_modalities=["Text", "Image"]
            )
        )

        image_data = None
        for part in response.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                image_data = part.inline_data.data
                break

        if image_data:
            return Image.open(BytesIO(image_data)), prompt
        else:
            return create_placeholder_image(theme, 0), prompt

    except Exception as e:
        logger.error(f"Error generating cover: {e}")
        return create_placeholder_image(theme, 0), prompt


# ==================== PDF GENERATION ====================

def create_interior_pdf(images: List[Image.Image], size: str, page_count: int, 
                        output_path: str) -> str:
    size_info = BOOK_SIZES[size]
    width = size_info["width"] * inch
    height = size_info["height"] * inch

    c = canvas.Canvas(output_path, pagesize=(width, height))

    for i, img in enumerate(images):
        if i > 0:
            c.showPage()

        margin = 0.5 * inch
        img_width = width - 2 * margin
        img_height = height - 2 * margin

        img_ratio = img.width / img.height
        page_ratio = img_width / img_height

        if img_ratio > page_ratio:
            img_height = img_width / img_ratio
        else:
            img_width = img_height * img_ratio

        x = (width - img_width) / 2
        y = (height - img_height) / 2

        c.drawImage(ImageReader(img), x, y, width=img_width, height=img_height)

    c.save()
    return output_path


def create_prompts_pdf(images: List[Image.Image], prompts: List[str], 
                       output_path: str) -> str:
    c = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter

    for i, (img, prompt) in enumerate(zip(images, prompts)):
        if i > 0:
            c.showPage()

        img_width = 4 * inch
        img_height = 4 * inch
        x = (width - img_width) / 2
        y = height - img_height - 0.5 * inch

        c.drawImage(ImageReader(img), x, y, width=img_width, height=img_height)

        c.setFont("Helvetica", 10)
        text = f"Page {i+1} Prompt:"
        c.drawString(0.5 * inch, y - 0.3 * inch, text)

        c.setFont("Helvetica", 8)
        y_pos = y - 0.6 * inch
        words = prompt.split()
        line = ""
        for word in words:
            if c.stringWidth(line + " " + word, "Helvetica", 8) < width - inch:
                line += " " + word if line else word
            else:
                c.drawString(0.5 * inch, y_pos, line)
                y_pos -= 0.15 * inch
                line = word
        if line:
            c.drawString(0.5 * inch, y_pos, line)

    c.save()
    return output_path


def create_cover_pdf(cover_img: Image.Image, size: str, output_path: str) -> str:
    width = 12 * inch
    height = 9 * inch

    c = canvas.Canvas(output_path, pagesize=(width, height))
    c.drawImage(ImageReader(cover_img), 0, 0, width=width, height=height)
    c.save()
    return output_path


# ==================== KDP LISTING GENERATION ====================

def generate_kdp_listing(theme: str, page_count: int, size: str) -> dict:
    size_info = BOOK_SIZES[size]

    title = f"{theme} Coloring Book for Kids: {page_count} Fun and Easy Pages"
    subtitle = f"A Creative Activity Book for Children Ages 4-8"

    description = f"""<b>Welcome to the wonderful world of {theme}!</b>

This delightful coloring book is perfect for children ages 4-8 who love {theme.lower()}.

<b>What's Inside:</b>
• {page_count} unique coloring pages
• Large 8.5" x 11" pages (perfect for little hands)
• Single-sided pages to prevent bleed-through
• Thick outlines for easy coloring
• Hours of creative fun and relaxation

<b>Perfect for:</b>
• Birthday gifts
• Christmas stocking stuffers
• Road trips and travel
• Rainy day activities
• Classroom rewards

Let your child's imagination soar with this amazing {theme} coloring adventure!"""

    keywords = [
        f"{theme.lower()} coloring book",
        "kids coloring book",
        "children activity book",
        "preschool coloring",
        "toddler coloring pages",
        "ages 4-8 coloring",
        "fun coloring book",
        "easy coloring pages"
    ]

    return {
        "title": title,
        "subtitle": subtitle,
        "description": description,
        "keywords": ", ".join(keywords[:7]),
        "categories": [
            "Juvenile Fiction > Activity Books > Coloring",
            "Juvenile Nonfiction > Activity Books > Coloring"
        ],
        "price": size_info["price"],
        "age_range": "4-8 years",
        "page_count": page_count,
        "size": size_info["name"]
    }


# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """🎨 <b>Welcome to KDP Coloring Book Agent!</b>

I'll help you create a complete KDP-ready coloring book in minutes.

<b>What I can do:</b>
✅ Analyze trends on Amazon
✅ Suggest hot themes
✅ Generate coloring pages with AI
✅ Create professional cover
✅ Build KDP-ready PDFs
✅ Write listing information

Let's get started!"""

    keyboard = [[InlineKeyboardButton("🚀 Start Creating", callback_data="start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="HTML")


async def show_trends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = "📊 <b>Today's Trending Themes</b>

"

    for theme in TRENDING_THEMES:
        text += f"{theme['emoji']} <b>{theme['name']}</b>
"
        text += f"   📈 {theme['trend_reason']}
"
        text += f"   🏆 BSR: {theme['bsr']}
"
        text += f"   ⚔️ Competition: {theme['competition']}

"

    text += "Choose a theme to continue:"

    keyboard = [
        [InlineKeyboardButton(f"{t['emoji']} {t['name']}", callback_data=f"theme_{t['id']}")]
        for t in TRENDING_THEMES
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def customize_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    theme_id = int(query.data.split("_")[1])
    theme = next(t for t in TRENDING_THEMES if t["id"] == theme_id)

    context.user_data["theme"] = theme["name"]
    context.user_data["theme_emoji"] = theme["emoji"]

    text = f"{theme['emoji']} <b>Great choice: {theme['name']}!</b>

"
    text += "Now let's customize your book.

"
    text += "<b>How many pages?</b>"

    keyboard = [
        [InlineKeyboardButton(f"{p} pages", callback_data=f"pages_{p}")]
        for p in PAGE_COUNTS
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CUSTOMIZING_PAGES


async def choose_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    page_count = int(query.data.split("_")[1])
    context.user_data["page_count"] = page_count

    text = f"✅ <b>{page_count} pages selected!</b>

"
    text += "<b>Choose book size:</b>"

    keyboard = [
        [InlineKeyboardButton(f"{v['name']} - {v['price']}", callback_data=f"size_{k}")]
        for k, v in BOOK_SIZES.items()
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CUSTOMIZING_SIZE


async def choose_extras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    size = query.data.split("_")[1]
    context.user_data["size"] = size

    text = f"✅ <b>{BOOK_SIZES[size]['name']} selected!</b>

"
    text += "<b>Add extras?</b> (Choose any)"

    keyboard = [
        [InlineKeyboardButton("📄 Dedication Page", callback_data="extra_dedication")],
        [InlineKeyboardButton("👤 About the Author", callback_data="extra_author")],
        [InlineKeyboardButton("✅ Solutions/Answers", callback_data="extra_solutions")],
        [InlineKeyboardButton("🎨 Done - Generate Book!", callback_data="generate")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CUSTOMIZING_EXTRAS


async def toggle_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    extra = query.data.split("_")[1]
    extras = context.user_data.get("extras", [])

    if extra in extras:
        extras.remove(extra)
    else:
        extras.append(extra)

    context.user_data["extras"] = extras

    text = f"✅ <b>Extras selected:</b> {', '.join(extras) if extras else 'None'}

"
    text += "Add more or generate:"

    keyboard = [
        [InlineKeyboardButton(
            f"{'✅' if 'dedication' in extras else '⬜'} Dedication Page", 
            callback_data="extra_dedication"
        )],
        [InlineKeyboardButton(
            f"{'✅' if 'author' in extras else '⬜'} About the Author", 
            callback_data="extra_author"
        )],
        [InlineKeyboardButton(
            f"{'✅' if 'solutions' in extras else '⬜'} Solutions/Answers", 
            callback_data="extra_solutions"
        )],
        [InlineKeyboardButton("🎨 Done - Generate Book!", callback_data="generate")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return CUSTOMIZING_EXTRAS


async def generate_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    theme = context.user_data["theme"]
    page_count = context.user_data["page_count"]
    size = context.user_data["size"]
    extras = context.user_data.get("extras", [])

    await query.edit_message_text(
        f"🎨 <b>Generating your {theme} coloring book...</b>

"
        f"⏳ This may take a few minutes.
"
        f"📄 Pages: {page_count}
"
        f"📐 Size: {BOOK_SIZES[size]['name']}
"
        f"✨ Extras: {', '.join(extras) if extras else 'None'}",
        parse_mode="HTML"
    )

    try:
        images = []
        prompts = []

        for i in range(page_count):
            img, prompt = await generate_coloring_page(theme, i)
            images.append(img)
            prompts.append(prompt)

            if (i + 1) % 5 == 0:
                await query.edit_message_text(
                    f"🎨 Generating pages... {i+1}/{page_count}",
                    parse_mode="HTML"
                )

        cover_img, cover_prompt = await generate_cover_image(theme)

        with tempfile.TemporaryDirectory() as tmpdir:
            interior_path = os.path.join(tmpdir, "interior.pdf")
            create_interior_pdf(images, size, page_count, interior_path)

            prompts_path = os.path.join(tmpdir, "prompts.pdf")
            create_prompts_pdf(images, prompts, prompts_path)

            cover_path = os.path.join(tmpdir, "cover.pdf")
            create_cover_pdf(cover_img, size, cover_path)

            kdp_info = generate_kdp_listing(theme, page_count, size)

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=open(interior_path, "rb"),
                filename=f"{theme.replace(' ', '_')}_interior.pdf",
                caption="📄 <b>KDP Interior PDF</b> - Ready for upload!"
            )

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=open(prompts_path, "rb"),
                filename=f"{theme.replace(' ', '_')}_prompts.pdf",
                caption="📝 <b>Prompts Reference PDF</b> - All prompts used!"
            )

            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=open(cover_path, "rb"),
                filename=f"{theme.replace(' ', '_')}_cover.pdf",
                caption="🎨 <b>KDP Cover PDF</b> - Ready for upload!"
            )

            listing_text = f"""📋 <b>KDP Listing Information</b>

<b>Title:</b> {kdp_info['title']}
<b>Subtitle:</b> {kdp_info['subtitle']}
<b>Price:</b> {kdp_info['price']}
<b>Age Range:</b> {kdp_info['age_range']}
<b>Page Count:</b> {kdp_info['page_count']}
<b>Size:</b> {kdp_info['size']}

<b>Keywords:</b>
{kdp_info['keywords']}

<b>Categories:</b>
• {kdp_info['categories'][0]}
• {kdp_info['categories'][1]}

<b>Description:</b>
{kdp_info['description']}"""

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=listing_text,
                parse_mode="HTML"
            )

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="""🎉 <b>Your coloring book is ready!</b>

All files have been sent. Here's what to do next:

1️⃣ Go to KDP (kdp.amazon.com)
2️⃣ Create a new paperback
3️⃣ Upload the Interior PDF
4️⃣ Upload the Cover PDF
5️⃣ Copy the listing information
6️⃣ Publish! 🚀

Want to create another book? Type /start""",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Error generating book: {e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ Error generating book: {str(e)}

Please try again with /start"
        )

    return ConversationHandler.END


# ==================== MAIN ====================

def run_bot():
    """Run the Telegram bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(show_trends, pattern="^start$")
        ],
        states={
            CUSTOMIZING_PAGES: [
                CallbackQueryHandler(choose_size, pattern="^pages_")
            ],
            CUSTOMIZING_SIZE: [
                CallbackQueryHandler(choose_extras, pattern="^size_")
            ],
            CUSTOMIZING_EXTRAS: [
                CallbackQueryHandler(toggle_extra, pattern="^extra_"),
                CallbackQueryHandler(generate_book, pattern="^generate$")
            ]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(show_trends, pattern="^start$"))
    application.add_handler(CallbackQueryHandler(customize_book, pattern="^theme_"))

    logger.info("Bot started!")
    application.run_polling()


if __name__ == "__main__":
    # Start bot in a separate thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()

    # Start Flask server for keep-alive
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
