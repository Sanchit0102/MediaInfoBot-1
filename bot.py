from pyrogram import Client, filters
from pyrogram.types import Message
from telegraph.aio import Telegraph
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
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAPH_TOKEN = os.getenv("TELEGRAPH_TOKEN")
CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
MAX_CHUNKS = 2  # Maximum number of chunks to analyze

# Initialize the bot and Telegraph
app = Client("MediaInfoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
telegraph = Telegraph(TELEGRAPH_TOKEN)

async def stream_media(message: Message, temp_path: str) -> Optional[str]:
    """Stream media in chunks and save only required portion for analysis."""
    try:
        media = message.document or message.video or message.audio
        if not media:
            return None

        async with aiofiles.open(temp_path, 'wb') as file:
            downloaded_chunks = 0
            async for chunk in app.stream_media(media, limit=MAX_CHUNKS):
                await file.write(chunk)
                downloaded_chunks += 1
                if downloaded_chunks >= MAX_CHUNKS:
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

def parse_mediainfo(output: str, file_name: str, file_size: int) -> str:
    """Parse MediaInfo output into Telegraph-compatible aesthetic format."""
    
    def clean_value(value: str) -> str:
        """Escape HTML special characters."""
        return value.replace('<', '&lt;').replace('>', '&gt;')
    
    html_parts = []
    
    # File Information Section
    html_parts.append("<h3>📁 File Information</h3>")
    html_parts.append(
        f"<p><strong>File Name:</strong> <em>{clean_value(file_name)}</em></p>"
        f"<p><strong>File Size:</strong> <em>{format_size(file_size)}</em></p>"
    )
    html_parts.append("<hr>")  # Horizontal line for separation
    
    current_section = ""
    section_lines = []
    
    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Section Header
        if ':' not in line:
            if section_lines:
                # Add the previous section's content
                html_parts.append("<pre>" + "\n".join(section_lines) + "</pre>")
                section_lines = []
            
            # New section header
            current_section = line
            emoji = SECTION_EMOJIS.get(current_section, '📝')
            html_parts.append(f"<h4>{emoji} {clean_value(current_section)}</h4>")
            continue
        
        # Key-Value Pair
        key, value = line.split(':', 1)
        key = clean_value(key.strip())
        value = clean_value(value.strip())
        section_lines.append(f"{key:20}: {value}")
    
    # Add the last section's content
    if section_lines:
        html_parts.append("<pre>" + "\n".join(section_lines) + "</pre>")
    
    # Footer Note
    html_parts.append("<p><em>Note: Analysis is based on initial file chunks.</em></p>")
    
    # Join all parts with proper spacing
    return "\n".join(html_parts)
    
# Updated SECTION_EMOJIS dictionary
SECTION_EMOJIS = {
    'General': '📄',
    'Video': '🎥',
    'Audio': '🔊',
    'Subtitles': '💬',
    'Format': '📦',
    'Chapters': '📑'
}

async def create_telegraph_page(title: str, content: str) -> str:
    """Create a Telegraph page with the media info with error handling."""
    try:
        # Clean the title for Telegraph
        clean_title = title[:128]  # Telegraph title length limit
        
        # Create page
        response = await telegraph.create_page(
            title=clean_title,
            html_content=content,
            author_name="MediaInfo Bot",
            author_url="https://t.me/your_bot_username"  # Replace with your bot's username
        )
        
        # Verify response
        if not response or 'path' not in response:
            logger.error(f"Invalid Telegraph response: {response}")
            return ""
            
        url = f"https://telegra.ph/{response['path']}"
        
        # Verify the page exists
        async with ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.error(f"Created page not accessible: {resp.status}")
                    return ""
        
        return url
    except Exception as e:
        logger.error(f"Error creating Telegraph page: {e}", exc_info=True)
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
        file_name = getattr(media, 'file_name', 'Unknown')
        file_size = getattr(media, 'file_size', 0)

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
            telegraph_url = await create_telegraph_page(
                title=f"MediaInfo: {file_name[:100]}", 
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
                await status_msg.edit_text("❌ Failed to create Telegraph page!")

        finally:
            # Cleanup
            try:
                os.unlink(temp_path)
            except:
                pass

    except Exception as e:
        logger.error(f"Error in mediainfo handler: {e}", exc_info=True)
        await message.reply_text("❌ An error occurred while processing the media info!")
# Start the bot
if __name__ == "__main__":
    print("Starting MediaInfo Bot...")
    app.run()
