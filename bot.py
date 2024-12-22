from pyrogram import Client, filters, idle
from pyrogram.types import Message
from aiohttp import ClientSession, web
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, path as aiopath, mkdir
from os import path as ospath, getcwd
import re, os, asyncio
from datetime import datetime
from telegraph import Telegraph
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot Configuration
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
TELEGRAPH_TOKEN = os.getenv('TELEGRAPH_TOKEN')
DOWNLOAD_DIR = "MediaInfo/"

# Initialize aiohttp web app
web_app = web.Application()

async def home_handler(request):
    """Handle root endpoint"""
    return web.Response(text="Bot is running!")

# Initialize Pyrogram Client
app = Client("mediainfo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize Telegraph
telegraph = Telegraph(TELEGRAPH_TOKEN)

async def create_download_dir():
    """Create download directory if it doesn't exist"""
    if not await aiopath.isdir(DOWNLOAD_DIR):
        await mkdir(DOWNLOAD_DIR)

async def download_from_url(url):
    """Download file from URL"""
    try:
        filename = re.search(r".+/(.+)", url).group(1)
        des_path = ospath.join(DOWNLOAD_DIR, filename)
        headers = {
            "user-agent": "Mozilla/5.0 (Linux; Android 12; 2201116PI) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"
        }
        async with ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None, None
                async with aiopen(des_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(10000000):
                        await f.write(chunk)
                        break
        return des_path, filename
    except Exception as e:
        print(f"Download error: {str(e)}")
        return None, None

async def download_from_telegram(message: Message, media):
    """Download file from Telegram"""
    try:
        filename = getattr(media, 'file_name', f'media_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        des_path = ospath.join(DOWNLOAD_DIR, filename)
        
        if media.file_size <= 50000000:  # 50MB
            await message.download(ospath.join(getcwd(), des_path))
        else:
            async for chunk in app.stream_media(media, limit=5):
                async with aiopen(des_path, "ab") as f:
                    await f.write(chunk)
        return des_path, filename
    except Exception as e:
        print(f"Telegram download error: {str(e)}")
        return None, None

async def get_mediainfo(file_path):
    """Get mediainfo output for file"""
    try:
        cmd = ["mediainfo", file_path]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return stdout.decode()
    except Exception as e:
        return f"Error getting mediainfo: {str(e)}"

def create_telegraph_content(filename, mediainfo_output):
    """Create formatted content for Telegraph"""
    content = []
    current_section = None
    
    section_emojis = {
        'General': '🗒',
        'Video': '🎞',
        'Audio': '🔊',
        'Text': '🔠',
        'Menu': '🗃',
        'Subtitle': '💬'
    }
    
    # Add file header
    content.append({
        'tag': 'h3',
        'children': [f'📋 MediaInfo Analysis for {filename}']
    })
    
    current_section_content = []
    
    for line in mediainfo_output.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Check for new section
        for section in section_emojis:
            if line.startswith(section):
                # Add previous section if exists
                if current_section_content:
                    content.extend(current_section_content)
                    current_section_content = []
                
                # Start new section
                current_section = section
                current_section_content.append({
                    'tag': 'h4',
                    'children': [f"{section_emojis[section]} {line}"]
                })
                current_section_content.append({
                    'tag': 'hr',
                    'children': []
                })
                break
        else:
            if ':' in line:
                key, value = line.split(':', 1)
                current_section_content.append({
                    'tag': 'p',
                    'children': [f"├─ <strong>{key.strip()}</strong>: {value.strip()}"]
                })
    
    # Add last section
    if current_section_content:
        content.extend(current_section_content)
    
    return content

async def create_telegraph_page(filename, mediainfo_output):
    """Create Telegraph page with mediainfo"""
    try:
        content = create_telegraph_content(filename, mediainfo_output)
        
        page = await telegraph.create_page(
            title=f'MediaInfo Analysis - {filename}',
            author_name='MediaInfo Bot',
            content=content
        )
        return f"https://telegra.ph/{page['path']}"
    except Exception as e:
        print(f"Telegraph error: {str(e)}")
        return None

def get_welcome_message():
    """Generate welcome message"""
    return (
        "👋 <b>Welcome to MediaInfo Bot!</b>\n\n"
        "I can provide detailed technical information about media files.\n\n"
        "<b>How to use me:</b>\n"
        "• Simply send me any media file\n"
        "• Or send me a direct download link\n\n"
        "<b>Supported Media Types:</b>\n"
        "• Videos\n"
        "• Audio files\n"
        "• Documents\n"
        "• Voice messages\n"
        "• Video notes\n"
        "• Animations/GIFs\n\n"
        "🔍 I'll analyze your media and provide a Telegraph link with detailed information!"
    )

async def handle_mediainfo(client: Client, message: Message):
    """Main handler for mediainfo"""

    status_msg = await message.reply_text("⏳ <b>Processing request...</b>")
    file_path = None
    
    try:
        await create_download_dir()
        
        # Check if message contains a URL
        urls = re.findall(r'(https?://\S+)', message.text) if message.text else []
        if urls:
            url = urls[0]
            await status_msg.edit_text("📥 <b>Downloading from URL...</b>")
            file_path, filename = await download_from_url(url)
        
        # Handle media file
        elif hasattr(message, 'media'):
            media = (message.document or message.video or message.audio or 
                    message.voice or message.animation or message.video_note)
            if media:
                await status_msg.edit_text("📥 <b>Downloading media file...</b>")
                file_path, filename = await download_from_telegram(message, media)
        
        if not file_path:
            await status_msg.delete()
            return
        
        # Get and process mediainfo
        await status_msg.edit_text("🔍 <b>Analyzing media file...</b>")
        mediainfo_output = await get_mediainfo(file_path)
        
        # Create Telegraph page
        await status_msg.edit_text("📝 <b>Creating Telegraph page...</b>")
        telegraph_url = await create_telegraph_page(filename, mediainfo_output)
        
        if telegraph_url:
            await status_msg.edit_text(
                f"✅ <b>MediaInfo Analysis Complete!</b>\n\n"
                f"📂 <b>File:</b> <code>{filename}</code>\n"
                f"🔍 <b>Analysis:</b> {telegraph_url}",
                disable_web_page_preview=False
            )
        else:
            await status_msg.edit_text("❌ <b>Failed to create Telegraph page.</b>")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> <code>{str(e)}</code>")
    
    finally:
        # Cleanup
        if file_path and await aiopath.exists(file_path):
            await aioremove(file_path)

# Register message handlers
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """Handle /start command"""
    await message.reply_text(get_welcome_message())

@app.on_message(filters.private & ~filters.command("start"))
async def handle_all_messages(client, message):
    """Handle all private messages"""
    # Process only if message contains media or URL
    if hasattr(message, 'media') or (message.text and re.search(r'(https?://\S+)', message.text)):
        await handle_mediainfo(client, message)

async def start_aiohttp():
    """Start aiohttp server"""
    # Setup routes
    web_app.router.add_get('/', home_handler)
    
    # Start server
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Web server started on http://0.0.0.0:8080")

async def main():
    """Main function to run both bot and web server"""
    # Start web server
    await start_aiohttp()
    
    # Start the bot
    await app.start()
    print("Starting MediaInfo Bot...")
    
    # Keep the bot running
    await idle()

if __name__ == "__main__":
    app.run()
