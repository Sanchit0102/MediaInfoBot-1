from pyrogram import Client, filters
from pyrogram.types import Message
from telegraph import Telegraph
from aiohttp import ClientSession
import os
import asyncio
import logging
from typing import Optional, Union
from datetime import datetime
import aiofiles
import tempfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot Configuration
class Config:
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    TELEGRAPH_TOKEN = os.getenv("TELEGRAPH_TOKEN")
    CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
    MAX_CHUNKS = 2  # Maximum number of chunks to analyze

# Initialize the bot and Telegraph
app = Client("MediaInfoBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN)
telegraph = Telegraph(Config.TELEGRAPH_TOKEN)

# Media info sections with emojis
SECTION_EMOJIS = {
    'General': '📄',
    'Video': '🎥',
    'Audio': '🔊',
    'Subtitle': '💬',
    'Chapters': '📑',
    'Format': '📦'
}

async def stream_media(message: Message, temp_path: str) -> Optional[str]:
    """Stream media in chunks and save only required portion for analysis."""
    try:
        media = message.document or message.video or message.audio
        if not media:
            return None

        async with aiofiles.open(temp_path, 'wb') as file:
            downloaded_chunks = 0
            async for chunk in app.stream_media(media, limit=Config.MAX_CHUNKS):
                await file.write(chunk)
                downloaded_chunks += 1
                if downloaded_chunks >= Config.MAX_CHUNKS:
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

def parse_mediainfo(output: str, file_name: str, file_size: int) -> str:
    """Parse mediainfo output into HTML format for Telegraph."""
    html_content = f"<h4>📁 File Information</h4><br>"
    html_content += f"<p><strong>File Name:</strong> {file_name}</p>"
    html_content += f"<p><strong>Total Size:</strong> {format_size(file_size)}</p><br>"
    
    current_section = ""
    section_content = ""
    
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            if section_content:
                html_content += f"{section_content}<br>"
                section_content = ""
            continue
            
        if ':' not in line:
            if section_content:
                html_content += f"{section_content}<br>"
                section_content = ""
            
            current_section = line
            emoji = SECTION_EMOJIS.get(current_section, '📝')
            html_content += f"<h4>{emoji} {current_section}</h4><br>"
            continue
            
        key, value = line.split(':', 1)
        section_content += f"<p><strong>{key.strip()}:</strong> {value.strip()}</p>"
    
    if section_content:
        html_content += f"{section_content}<br>"
    
    html_content += "<br><small>Note: Analysis based on initial file chunks</small>"
    return html_content

def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

async def create_telegraph_page(title: str, content: str) -> str:
    """Create a Telegraph page with the media info."""
    try:
        response = await telegraph.create_page(
            title=title,
            html_content=content,
            author_name="MediaInfo Bot"
        )
        return f"https://telegra.ph/{response['path']}"
    except Exception as e:
        logger.error(f"Error creating Telegraph page: {e}")
        return ""

@app.on_message(filters.command(["mediainfo", "mi"]))
async def handle_mediainfo(client: Client, message: Message):
    """Handle the mediainfo command."""
    try:
        replied = message.reply_to_message
        
        if not replied or not (replied.document or replied.video or replied.audio):
            await message.reply_text(
                "Please reply to a media file (document/video/audio) with /mediainfo or /mi"
            )
            return

        status_msg = await message.reply_text("⏳ Processing media info...")
        
        # Get media information
        media = replied.document or replied.video or replied.audio
        file_name = media.file_name or "Unknown"
        file_size = media.file_size

        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_path = temp_file.name

        try:
            # Stream media chunks
            downloaded_path = await stream_media(replied, temp_path)
            if not downloaded_path:
                await status_msg.edit_text("❌ Failed to stream media!")
                return

            # Get mediainfo
            mediainfo_output = await get_mediainfo(downloaded_path)
            if not mediainfo_output:
                await status_msg.edit_text("❌ Failed to get media information!")
                return

            # Parse mediainfo output
            html_content = parse_mediainfo(mediainfo_output, file_name, file_size)
            
            # Create Telegraph page
            title = f"MediaInfo - {file_name}"
            telegraph_url = await create_telegraph_page(title, html_content)
            
            if telegraph_url:
                await status_msg.edit_text(
                    f"📊 **Media Information**\n\n"
                    f"📁 **File:** `{file_name}`\n"
                    f"💾 **Size:** `{format_size(file_size)}`\n\n"
                    f"🔗 **Detailed Info:** {telegraph_url}",
                    disable_web_page_preview=False
                )
            else:
                await status_msg.edit_text("❌ Failed to create Telegraph page!")

        finally:
            # Cleanup
            try:
                os.unlink(temp_path)
            except:
                pass

    except Exception as e:
        logger.error(f"Error in mediainfo handler: {e}")
        await message.reply_text("❌ An error occurred while processing the media info!")

# Start the bot
if __name__ == "__main__":
    print("Starting MediaInfo Bot...")
    app.run()
