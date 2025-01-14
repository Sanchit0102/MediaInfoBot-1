from pyrogram import Client, filters
from pyrogram.types import Message
from telegraph.aio import Telegraph
from aiohttp import ClientSession
import os, asyncio, logging
from typing import Optional
import aiofiles, tempfile
from flask import Flask
from threading import Thread

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

async def stream_media(message: Message, temp_path: str, limit: int) -> Optional[str]:
    """Stream media in chunks and save only required portion for analysis."""
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

section_dict = {
    'General': '🗒',
    'Video': '🎞',
    'Audio': '🔊',
    'Text': '🔠',
    'Menu': '🗃'
}

def parse_mediainfo(out: str, file_name: str, file_size: int) -> str:
    """Parse MediaInfo output into Telegraph-compatible aesthetic format."""
    
    def clean_value(value: str) -> str:
        """Escape HTML special characters."""
        return value.replace('<', '&lt;').replace('>', '&gt;')
    
    html_parts = []
    html_parts.append("<h3>📁 File Information</h3>")
    html_parts.append(
        f"<p><strong>File Name:</strong> <em>{clean_value(file_name)}</em></p>"
        f"<p><strong>File Size:</strong> <em>{format_size(file_size)}</em></p>"
    )
    html_parts.append("<hr>")
    
    tc = ''
    trigger = False
    for line in out.split('\n'):
        line = line.strip()
        if not line:
            continue

        for section, emoji in section_dict.items():
            if line.startswith(section):
                trigger = True
                if not line.startswith('General'):
                    tc += '</pre><br>'
                tc += f"<h4>{emoji} {line.replace('Text', 'Subtitle')}</h4>"
                break

        if trigger:
            tc += '<br><pre>'
            trigger = False
        else:
            tc += clean_value(line) + '\n'
    
    tc += '</pre><br>'
    html_parts.append(tc)
    html_parts.append("<p><em>Note: Analysis is based on small chuncks of the files.</em></p>")
    
    return "\n".join(html_parts)

async def create_telegraph_page(title: str, content: str) -> str:
    """Create a Telegraph page with the media info with error handling."""
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
            return ""
            
        url = f"https://graph.org/{response['path']}"
        
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Created page not accessible: {resp.status}")
                    return ""
        
        return url
    except Exception as e:
        logger.error(f"Error creating Telegraph page: {e}", exc_info=True)
        return ""

@app.on_message(filters.command("start") & filters.private)
async def handel_start(client: Client, message: Message):
    await message.reply(
        f"__Hi, {message.from_user.mention}\nI can generate media info about your files. Just reply /mi or /mediainfo to a media files.__"
    )

@app.on_message(filters.command(["mediainfo", "mi"]))
async def handle_mediainfo(client: Client, message: Message):
    try:
        await handle_media(client, message)
    except Exception as e:
        logger.error(f"{e}")

@app.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def mediainfohandler(client, message):
    await handle_media(client, message)

async def handle_media(client, message):
    """Automatically fetch and respond with media info when media is sent in private chat."""
    try:
        replied = message.reply_to_message
        media = replied.document or replied.video or replied.audio
        
        if not replied or not (replied.document or replied.video or replied.audio):
            media = message.document or message.video or message.audio
            
        if not media:
            return await message.reply_text("__Please reply to a media file with /mediainfo or /mi__")

        # Inform the user that the bot is processing the media
        status_msg = await message.reply_text("⏳ __Processing media info...__")

        file_name = getattr(media, 'file_name', 'Unknown')
        file_size = getattr(media, 'file_size', 0)

        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = temp_file.name

        try:
            downloaded_path = await stream_media(message, temp_path, limit=5)
            if not downloaded_path:
                await status_msg.edit_text("❌ __Failed to stream media!__")
                return

            mediainfo_output = await get_mediainfo(downloaded_path)
            if not mediainfo_output:
                await status_msg.edit_text("❌ __Failed to get media information!__")
                return

            html_content = parse_mediainfo(mediainfo_output, file_name, file_size)
            
            telegraph_url = await create_telegraph_page(
                title=f"MetaDataInfo", 
                content=html_content
            )
            
            if telegraph_url:
                await status_msg.edit_text(
                    f"📊 **Media Information**\n\n"
                    f"📁 **File:** `{file_name}`\n"
                    f"💾 **Size:** `{format_size(file_size)}`\n\n"
                    f"🔗 **Detailed Info:** {telegraph_url}",
                    disable_web_page_preview=False
                )
            else:
                await status_msg.edit_text("❌ __Failed to create Telegraph page!__")

        finally:
            try:
                os.unlink(temp_path)
            except:
                pass

    except Exception as e:
        logger.error(f"Error handling media: {e}", exc_info=True)
        await message.reply_text("❌ __An error occurred while processing the media info!__")


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
