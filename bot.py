from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified
from telegraph.aio import Telegraph
from aiohttp import ClientSession
from typing import Optional, Union, Tuple
import aiofiles, tempfile 
from flask import Flask
from threading import Thread
import os, asyncio, logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAPH_TOKEN = os.getenv("TELEGRAPH_TOKEN")

app = Client("MediaInfoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
telegraph = Telegraph(TELEGRAPH_TOKEN)

async def stream_media(message: Message, temp_path: str, limit: int = 1) -> Optional[str]:
    """Stream media in chunks and save required portion for analysis."""
    try:
        media = message.document or message.video or message.audio
        if not media:
            return None

        async with aiofiles.open(temp_path, 'wb') as file:
            downloaded_chunks = 0
            async for chunk in app.stream_media(media, limit=limit):
                await file.write(chunk)
                downloaded_chunks += 1
                if downloaded_chunks >= limit:
                    break
        
        return temp_path
    except Exception as e:
        logger.error(f"Error streaming media: {e}")
        return None

async def get_mediainfo(file_path: str) -> str:
    """Get mediainfo output for the file."""
    try:
        process = await asyncio.create_subprocess_exec(
            'mediainfo', file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if stderr:
            logger.warning(f"MediaInfo stderr: {stderr.decode()}")
        return stdout.decode()
    except Exception as e:
        logger.error(f"Error getting mediainfo: {e}")
        return ""

def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

SECTION_ICONS = {
    'General': '🗒',
    'Video': '🎞',
    'Audio': '🔊',
    'Text': '🔠',
    'Subtitle': '🔠',
    'Menu': '🗃'
}

def parse_mediainfo(mediainfo_output: str, file_name: str, file_size: int) -> str:
    """Parse MediaInfo output into Telegraph-compatible format."""
    def clean_value(value: str) -> str:
        return value.replace('<', '&lt;').replace('>', '&gt;')

    html_parts = [
        "<h3>📁 File Information</h3>",
        f"<p><strong>File Name:</strong> <em>{clean_value(file_name)}</em></p>",
        f"<p><strong>File Size:</strong> <em>{format_size(file_size)}</em></p>",
        "<hr>"
    ]

    current_section = ""
    section_content = []

    for line in mediainfo_output.split('\n'):
        line = line.strip()
        if not line:
            continue

        is_section_header = False
        for section, emoji in SECTION_ICONS.items():
            if line.startswith(section):
                if line.startswith('Text'):
                    line = line.replace('Text', 'Subtitle')
                
                if current_section:
                    display_section = current_section.replace('Text', 'Subtitle') if current_section.startswith('Text') else current_section
                    
                    html_parts.extend([
                        f"<h4>{SECTION_ICONS.get(current_section, '📄')} {display_section}</h4>",
                        "<pre>",
                        "\n".join(section_content),
                        "</pre><br>"
                    ])
                current_section = line
                section_content = []
                is_section_header = True
                break

        if not is_section_header and current_section:
            section_content.append(clean_value(line))

    if current_section and section_content:
        display_section = current_section.replace('Text', 'Subtitle') if current_section.startswith('Text') else current_section
        html_parts.extend([
            f"<h4>{SECTION_ICONS.get(current_section, '📄')} {display_section}</h4>",
            "<pre>",
            "\n".join(section_content),
            "</pre><br>"
        ])

    html_parts.append("<p><em>Note: Analysis is based on initial portions of the file.</em></p>")
    return "\n".join(html_parts)

async def create_telegraph_page(title: str, content: str) -> Optional[str]:
    """Create a Telegraph page with media info and verify its accessibility."""
    try:
        clean_title = title[:128]
        
        response = await telegraph.create_page(
            title=clean_title,
            html_content=content,
            author_name="MetadataInfoBot",
            author_url="https://t.me/MetadataInfoBot"
        )
        
        if not response or 'path' not in response:
            logger.error(f"Invalid Telegraph response: {response}")
            return None
            
        url = f"https://graph.org/{response['path']}"
        
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Created page not accessible: {resp.status}")
                    return None
        
        return url
    except Exception as e:
        logger.error(f"Error creating Telegraph page: {e}", exc_info=True)
        return None

def get_media_from_message(message: Message):
    if message.reply_to_message:
        return (message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.audio)
    return message.document or message.video or message.audio
    
@app.on_message(filters.command(["start"]) & filters.private)
async def start_command(client: Client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add me in your group", url=f"https://t.me/MetadataInfoBot?startgroup=botsync&admin=manage_chat")]
    ])
    await message.reply_text(
        f"👋 Hi {message.from_user.mention}!\n\n"
        "I can analyze media files and provide detailed information.\n\n"
        "🔹 Send me any media file\n"
        "🔹 Or reply to a media with /mediainfo or /mi\n",
        reply_markup=keyboard
    )

async def process_media(message: Message):
    status_message = await message.reply_text("⏳ __Processing media info...__")
    try:
        media = get_media_from_message(message)
        if not media:
            await status_message.edit_text(
                "❌ __Please send a media file or reply to one with /mediainfo__"
            )
            return

        file_name = getattr(media, 'file_name', 'Unknown')
        file_size = getattr(media, 'file_size', 0)

        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = temp_file.name
            
        try:
            downloaded_path = await stream_media(message, temp_path)
            if not downloaded_path:
                await status_message.edit_text("❌ __Failed to download media sample!__")
                return

            await status_message.edit_text("🔍 __Analyzing media info...__")
            
            mediainfo_output = await get_mediainfo(downloaded_path)
            if not mediainfo_output:
                await status_message.edit_text("❌ __Failed to analyze media!__")
                return

            await status_message.edit_text("📝 __Generating report...__")
            
            html_content = parse_mediainfo(mediainfo_output, file_name, file_size)
            telegraph_url = await create_telegraph_page(
                title=f"Media Info",
                content=html_content
            )
            
            if telegraph_url:
                report_keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 Detailed Report", url=telegraph_url)
                ]])
                
                await status_message.edit_text(
                    f"📊 **Media Information**\n\n"
                    f"📁 **File:** `{file_name}`\n"
                    f"💾 **Size:** `{format_size(file_size)}`\n\n"
                    f"👉 **Detailed info:** {telegraph_url}",
                    reply_markup=report_keyboard,
                    disable_web_page_preview=False
                )
            else:
                await status_message.edit_text("❌ __Failed to generate report!__")

        finally:
            try:
                os.unlink(temp_path)
            except Exception as e:
                logger.error(f"Error removing temp file: {e}")

    except Exception as e:
        logger.error(f"Error processing media: {e}", exc_info=True)
        await status_message.edit_text(
            "❌ __An error occurred while processing the media!__\n"
            "Please try again later."
        )

@app.on_message(filters.command(["mediainfo", "mi"]))
async def mediainfo_command(client: Client, message: Message):
    """Handle /mediainfo and /mi commands."""
    if not (message.reply_to_message and 
            (message.reply_to_message.document or 
             message.reply_to_message.video or 
             message.reply_to_message.audio)):
        await message.reply_text(
            "❌ __Please reply to a media file with /mediainfo or /mi__"
        )
        return
    await process_media(message.reply_to_message)

@app.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def media_handler(client: Client, message: Message):
    await process_media(message)

# Web server
web = Flask(__name__)

@web.route('/')
def index():
    return "Bot is running!"

def run():
    web.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

if __name__ == "__main__":
    print("Starting MediaInfo Bot...")
    Thread(target=run).start()
    app.run()
