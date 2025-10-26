import asyncio
import logging
import sqlite3
from datetime import datetime
import pytz
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserNotParticipant, PeerIdInvalid
import time
import random
import os

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Helper function for progress bar
def get_progress_bar(progress, total, width=20, style="block"):
    if total == 0:
        return "─" * width
    filled = int(width * min(progress, total) / total)
    if style == "block":
        return "█" * filled + "─" * (width - filled)
    elif style == "circle":
        return "●" * filled + "○" * (width - filled)
    elif style == "arrow":
        return "➔" * filled + "—" * (width - filled)
    return "█" * filled + "─" * (width - filled)

# Helper function for formatting timestamps
def format_datetime(dt_str):
    try:
        dt = datetime.fromisoformat(dt_str.replace(" ", "T"))
        return dt.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")
    except:
        return dt_str[:16]

class UserbotMemberManager:
    def __init__(self):
        self.setup_database()
        
    def setup_database(self):
        """Initialize database to store members and operations"""
        self.conn = sqlite3.connect('userbot_member_storage.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        # Table for storing source groups and their members
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER UNIQUE,
                group_title TEXT,
                member_count INTEGER,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Table for storing members from source groups
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_group_id INTEGER,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                is_bot INTEGER DEFAULT 0,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_group_id) REFERENCES source_groups (id)
            )
        ''')
        
        # Table for tracking transfer operations
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transfer_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_group_id INTEGER,
                target_group_id INTEGER,
                members_transferred INTEGER,
                total_members INTEGER,
                status TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                FOREIGN KEY (source_group_id) REFERENCES source_groups (id)
            )
        ''')
        
        # Table for user states
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                state TEXT,
                source_id INTEGER,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Backup tables for undo
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_source_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER UNIQUE,
                group_title TEXT,
                member_count INTEGER,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_group_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_group_id INTEGER,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                is_bot INTEGER DEFAULT 0,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_transfer_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_group_id INTEGER,
                target_group_id INTEGER,
                members_transferred INTEGER,
                total_members INTEGER,
                status TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        ''')
        
        self.conn.commit()

    async def start_command(self, client: Client, message: Message):
        """Send welcome message with inline buttons for conversational flow"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state) VALUES (?, ?)",
            (message.from_user.id, "start")
        )
        self.conn.commit()
        welcome_text = """
🤖 **Welcome to Member Manager Bot** 🌟
Let's begin! What would you like to do?
        """
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Save Members", callback_data="start_save_members")],
            [InlineKeyboardButton("📤 Add Members", callback_data="start_add_members")],
            [InlineKeyboardButton("📊 View Status", callback_data="status")],
            [InlineKeyboardButton("📋 List Sources", callback_data="list_sources")]
        ])
        await message.reply_text(welcome_text, parse_mode="markdown", reply_markup=keyboard)

    async def save_members_command(self, client: Client, message: Message):
        """Save members from source group to database with state check"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT state FROM user_states WHERE user_id = ?", (message.from_user.id,))
        state = cursor.fetchone()
        if not state or state[0] != "awaiting_source":
            await message.reply_text("❌ **Please start the process with `.start` or select 'Save Members'.** 🚨", parse_mode="markdown")
            return
        if len(message.command) < 2:
            await message.reply_text(
                "❌ **Usage:** `.save_members <group_id_or_link>`\n\n"
                "Example: `.save_members -100123456789` or `.save_members @groupusername` or `.save_members https://t.me/+abcdef`",
                parse_mode="markdown"
            )
            return

        group_identifier = message.command[1]
        logger.info(f"Attempting to save members from group: {group_identifier}")
        status_msg = await message.reply_text("🔄 **Collecting members from source group...** 🌐", parse_mode="markdown")

        try:
            # Get chat object
            try:
                if group_identifier.startswith("https://t.me/+"):
                    chat = await client.join_chat(group_identifier)
                else:
                    chat = await client.get_chat(group_identifier)
            except PeerIdInvalid:
                await status_msg.edit_text("❌ **Invalid group ID or link. Please check and try again.** ❌", parse_mode="markdown")
                return
            except Exception as e:
                await status_msg.edit_text(f"❌ **Cannot access group:** {str(e)} ❌", parse_mode="markdown")
                return

            if chat.type not in ["group", "supergroup"]:
                await status_msg.edit_text("❌ **This is not a group or supergroup!** ❌", parse_mode="markdown")
                return

            group_id = chat.id
            group_title = chat.title

            try:
                member = await client.get_chat_member(group_id, "me")
                if member.status not in ["creator", "administrator", "member"]:
                    await status_msg.edit_text("❌ **I'm not a member of this group!** ❌", parse_mode="markdown")
                    return
            except UserNotParticipant:
                await status_msg.edit_text("❌ **I'm not a member of this group!** ❌", parse_mode="markdown")
                return

            cursor.execute("SELECT id FROM source_groups WHERE group_id = ?", (group_id,))
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("DELETE FROM group_members WHERE source_group_id = ?", (existing[0],))
                cursor.execute("UPDATE source_groups SET member_count = 0 WHERE id = ?", (existing[0],))
                source_group_db_id = existing[0]
            else:
                cursor.execute(
                    "INSERT INTO source_groups (group_id, group_title, member_count) VALUES (?, ?, ?)",
                    (group_id, group_title, 0)
                )
                source_group_db_id = cursor.lastrowid
            
            self.conn.commit()
            
            await status_msg.edit_text("📥 **Collecting members... This may take a while...** ⏳", parse_mode="markdown")
            
            saved_count = await self.save_actual_members(client, source_group_db_id, group_id, status_msg)
            
            cursor.execute("UPDATE source_groups SET member_count = ? WHERE id = ?", (saved_count, source_group_db_id))
            self.conn.commit()
            
            await status_msg.edit_text(
                f"✅ **Members Saved Successfully!** 🎉\n\n"
                f"**Group:** {group_title}\n"
                f"**Saved Members:** {saved_count}\n"
                f"**Source ID:** `{source_group_db_id}`\n\n"
                f"Now use: `.add_members <target_group_id>` 🚀",
                parse_mode="markdown"
            )
            
            # Update state after success
            cursor.execute(
                "INSERT OR REPLACE INTO user_states (user_id, state, source_id) VALUES (?, ?, ?)",
                (message.from_user.id, "source_saved", source_group_db_id)
            )
            self.conn.commit()
            
        except Exception as e:
            await status_msg.edit_text(f"❌ **Error saving members:** {str(e)} ❌", parse_mode="markdown")
            logger.error(f"Error in save_members: {e}")
            await self.notify_admin(client, f"Error saving members from {group_identifier}: {str(e)}")

    async def save_actual_members(self, client: Client, db_id: int, group_id: int, status_msg: Message) -> int:
        """Save actual group members to database with real-time progress and cancel"""
        saved_count = 0
        cursor = self.conn.cursor()
        batch = []
        cursor.execute(
            "INSERT INTO transfer_operations (source_group_id, status, total_members) VALUES (?, ?, ?)",
            (db_id, "saving", 0)
        )
        operation_id = cursor.lastrowid
        self.conn.commit()
        
        try:
            async for member in client.get_chat_members(group_id):
                user = member.user
                if user.is_bot:
                    continue
                batch.append((db_id, user.id, user.username, user.first_name, user.last_name or "", 0))
                saved_count += 1
                if len(batch) >= 100:
                    cursor.executemany(
                        "INSERT INTO group_members (source_group_id, user_id, username, first_name, last_name, is_bot) VALUES (?, ?, ?, ?, ?, ?)",
                        batch
                    )
                    self.conn.commit()
                    batch = []
                    if saved_count % 50 == 0:
                        progress_bar = get_progress_bar(saved_count, 1000, style="circle")
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_save_{operation_id}")]
                        ])
                        await status_msg.edit_text(
                            f"📥 **Collecting {saved_count} members...**\n`{progress_bar}`",
                            parse_mode="markdown",
                            reply_markup=keyboard
                        )
                await asyncio.sleep(0.1)
            if batch:
                cursor.executemany(
                    "INSERT INTO group_members (source_group_id, user_id, username, first_name, last_name, is_bot) VALUES (?, ?, ?, ?, ?, ?)",
                    batch
                )
                self.conn.commit()
            cursor.execute(
                "UPDATE transfer_operations SET status = ?, members_transferred = ? WHERE id = ?",
                ("completed", saved_count, operation_id)
            )
            self.conn.commit()
        except FloodWait as e:
            for i in range(e.value, 0, -1):
                await status_msg.edit_text(f"⏳ **Flood wait:** {i} seconds remaining... ⏲️", parse_mode="markdown")
                await asyncio.sleep(1)
            return await self.save_actual_members(client, db_id, group_id, status_msg)
        except Exception as e:
            cursor.execute("UPDATE transfer_operations SET status = ? WHERE id = ?", ("failed", operation_id))
            self.conn.commit()
            logger.error(f"Error in save_actual_members: {e}")
            await self.notify_admin(client, f"Error saving members for group ID {group_id}: {str(e)}")
        
        return saved_count

    async def add_members_to_target(self, client: Client, members: list, target_group_id: int, status_msg: Message, operation_id: int) -> int:
        """Add members to target group with proper rate limiting"""
        success_count = 0
        total_members = len(members)
        
        for index, (user_id, username, first_name) in enumerate(members):
            try:
                await client.add_chat_members(target_group_id, user_id)
                success_count += 1
                if (index + 1) % 5 == 0 or (index + 1) == total_members:
                    progress_bar = get_progress_bar(success_count, total_members, style="arrow")
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_add_{operation_id}")]
                    ])
                    await status_msg.edit_text(
                        f"🔄 **Adding members...** 📤\n"
                        f"**Progress:** {index + 1}/{total_members}\n"
                        f"**Successful:** {success_count} ✅\n"
                        f"`{progress_bar}`",
                        parse_mode="markdown",
                        reply_markup=keyboard
                    )
                cursor = self.conn.cursor()
                cursor.execute(
                    "UPDATE transfer_operations SET members_transferred = ? WHERE id = ?",
                    (success_count, operation_id)
                )
                self.conn.commit()
                delay = random.uniform(2, 5)
                await asyncio.sleep(delay)
            except FloodWait as e:
                for i in range(e.value, 0, -1):
                    await status_msg.edit_text(
                        f"⏳ **Flood wait:** {i} seconds remaining... ⏲️",
                        parse_mode="markdown"
                    )
                    await asyncio.sleep(1)
                continue
            except Exception as e:
                logger.error(f"Failed to add user {user_id}: {e}")
                await self.notify_admin(client, f"Failed to add user {user_id} to group {target_group_id}: {str(e)}")
                continue
        
        return success_count

    async def add_members_command(self, client: Client, message: Message):
        """Add saved members to target group with dynamic source selection"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT state, source_id FROM user_states WHERE user_id = ?", (message.from_user.id,))
        state = cursor.fetchone()
        
        if len(message.command) < 2 and (not state or state[0] != "awaiting_target"):
            cursor.execute("SELECT id, group_title FROM source_groups ORDER BY saved_at DESC")
            sources = cursor.fetchall()
            if not sources:
                await message.reply_text("❌ **No saved sources. Use `.save_members` first.** 🚨", parse_mode="markdown")
                return
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{title} (ID: {id})", callback_data=f"select_source_{id}")]
                for id, title in sources[:5]
            ] + [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
            await message.reply_text("📤 **Select a source group to add members from:**", parse_mode="markdown", reply_markup=keyboard)
            return
        
        if state and state[0] == "awaiting_target":
            target_identifier = message.command[1] if len(message.command) >= 2 else None
            if not target_identifier:
                await message.reply_text("❌ **Please provide a target group ID or link.** 🚨", parse_mode="markdown")
                return
            source_db_id = state[1]
            cursor.execute(
                "SELECT group_id, group_title, member_count FROM source_groups WHERE id = ?",
                (source_db_id,)
            )
            source_data = cursor.fetchone()
            if not source_data:
                await message.reply_text("❌ **Invalid source ID.** 🚨", parse_mode="markdown")
                return
            source_group_id, group_title, member_count = source_data
            
            if member_count == 0:
                await message.reply_text("❌ **No members saved for this group!** 🚨", parse_mode="markdown")
                return
            
            status_msg = await message.reply_text(
                f"🔄 **Preparing to add {member_count} members to target group...** 📤",
                parse_mode="markdown"
            )
            
            try:
                try:
                    target_chat = await client.get_chat(target_identifier)
                    if target_chat.type not in ["group", "supergroup"]:
                        await status_msg.edit_text("❌ **Target must be a group or supergroup!** ❌", parse_mode="markdown")
                        return
                    target_group_id = target_chat.id
                except Exception as e:
                    await status_msg.edit_text(f"❌ **Cannot access target group:** {str(e)} ❌", parse_mode="markdown")
                    await self.notify_admin(client, f"Cannot access target group {target_identifier}: {str(e)}")
                    return
                
                try:
                    target_member = await client.get_chat_member(target_group_id, "me")
                    if target_member.status not in ["creator", "administrator"]:
                        await status_msg.edit_text("❌ **I must be admin in the target group!** ❌", parse_mode="markdown")
                        return
                except Exception as e:
                    await status_msg.edit_text(f"❌ **Cannot check admin status:** {str(e)} ❌", parse_mode="markdown")
                    await self.notify_admin(client, f"Cannot check admin status for group {target_group_id}: {str(e)}")
                    return
                
                cursor.execute(
                    "SELECT user_id, username, first_name FROM group_members WHERE source_group_id = ?",
                    (source_db_id,)
                )
                members = cursor.fetchall()
                
                cursor.execute(
                    "INSERT INTO transfer_operations (source_group_id, target_group_id, members_transferred, total_members, status) VALUES (?, ?, ?, ?, ?)",
                    (source_db_id, target_group_id, 0, len(members), 'started')
                )
                operation_id = cursor.lastrowid
                self.conn.commit()
                
                success_count = await self.add_members_to_target(
                    client, members, target_group_id, status_msg, operation_id
                )
                
                cursor.execute(
                    "UPDATE transfer_operations SET members_transferred = ?, status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (success_count, 'completed', operation_id)
                )
                self.conn.commit()
                
                await status_msg.edit_text(
                    f"✅ **Transfer Completed!** 🎊\n\n"
                    f"**Source:** {group_title}\n"
                    f"**Target:** {target_chat.title}\n"
                    f"**Successfully Added:** {success_count}/{member_count} members\n"
                    f"**Failed:** {member_count - success_count} members\n"
                    f"`{get_progress_bar(success_count, member_count, style='arrow')}` 🚀",
                    parse_mode="markdown"
                )
                
                # Reset state after success
                cursor.execute("DELETE FROM user_states WHERE user_id = ?", (message.from_user.id,))
                self.conn.commit()
                
            except Exception as e:
                cursor.execute(
                    "UPDATE transfer_operations SET status = ? WHERE id = ?",
                    ('failed', operation_id)
                )
                self.conn.commit()
                await status_msg.edit_text(f"❌ **Error during transfer:** {str(e)} ❌", parse_mode="markdown")
                logger.error(f"Error in add_members: {e}")
                await self.notify_admin(client, f"Error during transfer to {target_identifier}: {str(e)}")

    async def list_sources_command(self, client: Client, message: Message):
        """List all saved source groups in a stylish table"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, group_id, group_title, member_count, saved_at FROM source_groups ORDER BY saved_at DESC"
        )
        sources = cursor.fetchall()
        
        if not sources:
            await message.reply_text("📭 **No saved source groups found.** 😔", parse_mode="markdown")
            return
        
        response = "📋 **Saved Source Groups:** 🌟\n\n```\n"
        response += f"{'ID':<5} | {'Group Title':<25} | {'Members':<8} | {'Saved At':<19} | {'Progress':<20}\n"
        response += "-" * 80 + "\n"
        for db_id, group_id, title, count, saved_at in sources:
            title = (title[:25] + "...") if title and len(title) > 25 else title or "Unknown"
            saved_at = format_datetime(saved_at)
            progress_bar = get_progress_bar(count, 1000, style="circle")
            response += f"{db_id:<5} | {title:<25} | {count:<8} | {saved_at:<19} | {progress_bar:<20}\n"
        response += "```\n"
        
        await message.reply_text(response, parse_mode="markdown")

    async def list_members_command(self, client: Client, message: Message):
        """List members from a specific source group in a stylish list"""
        if len(message.command) < 2:
            await message.reply_text("❌ **Usage:** `.list_members <source_id>` 🚨", parse_mode="markdown")
            return
        
        try:
            source_id = int(message.command[1])
        except ValueError:
            await message.reply_text("❌ **Invalid source ID.** ❌", parse_mode="markdown")
            return
        
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT username, first_name, last_name, is_bot FROM group_members WHERE source_group_id = ? LIMIT 30",
            (source_id,)
        )
        members = cursor.fetchall()
        
        if not members:
            await message.reply_text("❌ **No members found for this source ID.** 😔", parse_mode="markdown")
            return
        
        response = f"👥 **Members (Source ID: {source_id}):** 🌟\n\n"
        for username, first_name, last_name, is_bot in members:
            name = f"{first_name} {last_name or ''}".strip()
            bot_indicator = " 🤖" if is_bot else ""
            username_str = f"@{username}" if username else "No username"
            response += f"• **{name}** ({username_str}){bot_indicator}\n"
        
        if len(members) == 30:
            response += f"\n**... and more (showing first 30)** 📜"
        
        await message.reply_text(response, parse_mode="markdown")

    async def status_command(self, client: Client, message: Message, page=1):
        """Check operations status with A to Z details, progress bars, pagination, and progressive disclosure"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM group_members")
        total_members = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM source_groups")
        sources_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM transfer_operations")
        operations_count = cursor.fetchone()[0]
        
        status_text = f"""
📊 **Userbot Status Overview (Page {page})** 🌟
**Total Saved Members:** {total_members} 👥
**Saved Sources:** {sources_count} 📥
**Transfer Operations:** {operations_count} 📤
Select a section to view details:
        """
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 View Saved Sources", callback_data=f"show_sources_{page}")],
            [InlineKeyboardButton("📤 View Transfer History", callback_data=f"show_transfers_{page}")],
            [InlineKeyboardButton("📄 Export as CSV", callback_data="export_status")]
        ])
        await message.reply_text(status_text, parse_mode="markdown", reply_markup=keyboard)

    async def show_status_section(self, client: Client, callback_query, section: str, page: int):
        """Show paginated status section (sources or transfers)"""
        cursor = self.conn.cursor()
        status_text = f"📊 **{section.capitalize()} Details (Page {page})** 🌟\n\n"
        
        items_per_page = 5
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        
        if section == "sources":
            cursor.execute("SELECT id, group_title, member_count, saved_at FROM source_groups ORDER BY saved_at DESC")
            sources = cursor.fetchall()
            total_pages = (len(sources) + items_per_page - 1) // items_per_page
            page = min(max(1, page), total_pages)
            if sources:
                status_text += "```\n"
                status_text += f"{'ID':<5} | {'Group Title':<25} | {'Members':<8} | {'Saved At':<19} | {'Progress':<20}\n"
                status_text += "-" * 80 + "\n"
                for db_id, title, count, saved_at in sources[start_idx:end_idx]:
                    title = (title[:25] + "...") if title and len(title) > 25 else title or "Unknown"
                    saved_at = format_datetime(saved_at)
                    progress_bar = get_progress_bar(count, 1000, style="circle")
                    status_text += f"{db_id:<5} | {title:<25} | {count:<8} | {saved_at:<19} | {progress_bar:<20}\n"
                status_text += "```\n"
            else:
                status_text += "**No saved sources.** 😔\n"
        
        elif section == "transfers":
            cursor.execute('''
                SELECT s.group_title, t.target_group_id, t.members_transferred, t.total_members, t.status, t.started_at, t.completed_at
                FROM transfer_operations t
                JOIN source_groups s ON t.source_group_id = s.id
                ORDER BY t.started_at DESC
            ''')
            operations = cursor.fetchall()
            total_pages = (len(operations) + items_per_page - 1) // items_per_page
            page = min(max(1, page), total_pages)
            if operations:
                status_text += "```\n"
                status_text += f"{'Source':<25} | {'Target':<25} | {'Transferred':<12} | {'Status':<10} | {'Started':<19} | {'Progress':<20}\n"
                status_text += "-" * 105 + "\n"
                for title, target_id, transferred, total_members, status, started, completed in operations[start_idx:end_idx]:
                    title = (title[:25] + "...") if title and len(title) > 25 else title or "Unknown"
                    try:
                        target_chat = await client.get_chat(target_id)
                        target_title = (target_chat.title[:25] + "...") if target_chat.title and len(target_chat.title) > 25 else target_chat.title or "Unknown"
                    except:
                        target_title = str(target_id)
                    status_icon = "✅" if status == "completed" else "🔄" if status == "started" else "❌"
                    started = format_datetime(started)
                    progress_bar = get_progress_bar(transferred, total_members if total_members else 1000, style="arrow")
                    status_text += f"{title:<25} | {target_title:<25} | {transferred}/{total_members or 'N/A':<12} | {status_icon:<10} | {started:<19} | {progress_bar:<20}\n"
                status_text += "```\n"
            else:
                status_text += "**No transfer operations.** 😔\n"
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("◄ Previous", callback_data=f"show_{section}_{page-1}") if page > 1 else InlineKeyboardButton(" ", callback_data="noop"),
                InlineKeyboardButton("🔙 Back to Overview", callback_data="status"),
                InlineKeyboardButton("Next ►", callback_data=f"show_{section}_{page+1}") if page < total_pages else InlineKeyboardButton(" ", callback_data="noop")
            ]
        ])
        await callback_query.message.edit_text(status_text, parse_mode="markdown", reply_markup=keyboard)

    async def clear_data_command(self, client: Client, message: Message):
        """Clear all saved data with undo option"""
        cursor = self.conn.cursor()
        # Backup data
        cursor.execute("DELETE FROM backup_source_groups")
        cursor.execute("DELETE FROM backup_group_members")
        cursor.execute("DELETE FROM backup_transfer_operations")
        cursor.execute("INSERT INTO backup_source_groups SELECT * FROM source_groups")
        cursor.execute("INSERT INTO backup_group_members SELECT * FROM group_members")
        cursor.execute("INSERT INTO backup_transfer_operations SELECT * FROM transfer_operations")
        # Clear data
        cursor.execute("DELETE FROM group_members")
        cursor.execute("DELETE FROM source_groups")
        cursor.execute("DELETE FROM transfer_operations")
        self.conn.commit()
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Undo Clear", callback_data="undo_clear")]
        ])
        await message.reply_text("🗑️ **All saved data has been cleared!** ✅\nYou can undo this action within 5 minutes.", parse_mode="markdown", reply_markup=keyboard)
        
        # Schedule backup deletion
        asyncio.create_task(self.delete_backup_after_delay(client, message.from_user.id))

    async def delete_backup_after_delay(self, client: Client, user_id: int, delay=300):
        """Delete backup data after delay"""
        await asyncio.sleep(delay)
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM backup_source_groups")
        cursor.execute("DELETE FROM backup_group_members")
        cursor.execute("DELETE FROM backup_transfer_operations")
        self.conn.commit()
        await client.send_message(user_id, "🕒 **Undo period for clear data has expired.**", parse_mode="markdown")

    async def undo_clear(self, client: Client, callback_query):
        """Restore cleared data from backup"""
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO source_groups SELECT * FROM backup_source_groups")
        cursor.execute("INSERT INTO group_members SELECT * FROM backup_group_members")
        cursor.execute("INSERT INTO transfer_operations SELECT * FROM backup_transfer_operations")
        cursor.execute("DELETE FROM backup_source_groups")
        cursor.execute("DELETE FROM backup_group_members")
        cursor.execute("DELETE FROM backup_transfer_operations")
        self.conn.commit()
        await callback_query.message.edit_text("🔄 **Data restored successfully!** ✅", parse_mode="markdown")

    async def notify_admin(self, client: Client, error_message: str):
        """Notify admin of critical errors"""
        await client.send_message(
            chat_id=ADMIN_ID,
            text=f"🚨 **Critical Error:**\n{error_message}",
            parse_mode="markdown"
        )

# Pyrogram Client Setup
app = Client(
    "member_manager_userbot",
    api_id=int(os.getenv("API_ID", "27769778")),
    api_hash=os.getenv("API_HASH", "b14c7b82cb09e90706ff61f02a2b46aa")
)

userbot_manager = UserbotMemberManager()

# Define admin ID
ADMIN_ID = 7848273230  # Replace with the actual admin user ID

# Command handlers with admin check
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.start_command(client, message)

@app.on_message(filters.command("save_members") & filters.private)
async def save_members_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.save_members_command(client, message)

@app.on_message(filters.command("add_members") & filters.private)
async def add_members_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.add_members_command(client, message)

@app.on_message(filters.command("list_sources") & filters.private)
async def list_sources_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.list_sources_command(client, message)

@app.on_message(filters.command("list_members") & filters.private)
async def list_members_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.list_members_command(client, message)

@app.on_message(filters.command("status") & filters.private)
async def status_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.status_command(client, message)

@app.on_message(filters.command("clear_data") & filters.private)
async def clear_data_handler(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply_text("❌ **You are not authorized to use this command.** 🔒", parse_mode="markdown")
        return
    await userbot_manager.clear_data_command(client, message)

@app.on_message(filters.command("myid") & filters.private)
async def myid_handler(client: Client, message: Message):
    """Temporary command to get user ID"""
    await message.reply_text(f"Your User ID: `{message.from_user.id}`", parse_mode="markdown")

@app.on_callback_query()
async def handle_callback(client: Client, callback_query):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized!", show_alert=True)
        return
    data = callback_query.data
    cursor = userbot_manager.conn.cursor()
    
    if data == "start_save_members":
        cursor.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state) VALUES (?, ?)",
            (callback_query.from_user.id, "awaiting_source")
        )
        userbot_manager.conn.commit()
        await callback_query.message.reply_text(
            "📥 **Enter source group ID or link:**\nExample: `.save_members @MyGroup`",
            parse_mode="markdown"
        )
    elif data == "start_add_members":
        cursor.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state) VALUES (?, ?)",
            (callback_query.from_user.id, "awaiting_target")
        )
        userbot_manager.conn.commit()
        await callback_query.message.reply_text(
            "📤 **Enter target group ID or link:**\nExample: `.add_members -100987654321`",
            parse_mode="markdown"
        )
    elif data == "status":
        await userbot_manager.status_command(client, callback_query.message)
    elif data.startswith("show_sources_"):
        page = int(data.split("_")[-1])
        await userbot_manager.show_status_section(client, callback_query, "sources", page)
    elif data.startswith("show_transfers_"):
        page = int(data.split("_")[-1])
        await userbot_manager.show_status_section(client, callback_query, "transfers", page)
    elif data.startswith("status_page_"):
        page = int(data.split("_")[-1])
        await userbot_manager.status_command(client, callback_query.message, page=page)
    elif data == "list_sources":
        await userbot_manager.list_sources_command(client, callback_query.message)
    elif data.startswith("select_source_"):
        source_id = int(data.split("_")[-1])
        cursor.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state, source_id) VALUES (?, ?, ?)",
            (callback_query.from_user.id, "awaiting_target", source_id)
        )
        userbot_manager.conn.commit()
        await callback_query.message.reply_text(
            "📤 **Enter target group ID or link:**\nExample: `.add_members -100987654321`",
            parse_mode="markdown"
        )
    elif data == "cancel":
        cursor.execute("DELETE FROM user_states WHERE user_id = ?", (callback_query.from_user.id,))
        userbot_manager.conn.commit()
        await callback_query.message.reply_text("✅ **Operation cancelled.**", parse_mode="markdown")
    elif data.startswith("cancel_save_"):
        operation_id = int(data.split("_")[-1])
        cursor.execute("UPDATE transfer_operations SET status = ? WHERE id = ?", ("cancelled", operation_id))
        userbot_manager.conn.commit()
        await callback_query.message.edit_text("❌ **Save operation cancelled.**", parse_mode="markdown")
    elif data.startswith("cancel_add_"):
        operation_id = int(data.split("_")[-1])
        cursor.execute("UPDATE transfer_operations SET status = ? WHERE id = ?", ("cancelled", operation_id))
        userbot_manager.conn.commit()
        await callback_query.message.edit_text("❌ **Add operation cancelled.**", parse_mode="markdown")
    elif data == "undo_clear":
        await userbot_manager.undo_clear(client, callback_query)
    elif data == "export_status":
        cursor = userbot_manager.conn.cursor()
        cursor.execute('''
            SELECT s.group_title, t.target_group_id, t.members_transferred, t.total_members, t.status, t.started_at, t.completed_at
            FROM transfer_operations t
            JOIN source_groups s ON t.source_group_id = s.id
            ORDER BY t.started_at DESC
        ''')
        operations = cursor.fetchall()
        
        csv_content = "Source,Target,Transferred,Total,Status,Started,Completed\n"
        for title, target_id, transferred, total_members, status, started, completed in operations:
            try:
                target_chat = await client.get_chat(target_id)
                target_title = target_chat.title or str(target_id)
            except:
                target_title = str(target_id)
            total = total_members if total_members is not None else "N/A"
            completed_str = format_datetime(completed) if completed else ""
            csv_content += f'"{title}","{target_title}",{transferred},{total},{status},{format_datetime(started)},{completed_str}\n'
        
        with open("status_export.csv", "w", encoding="utf-8") as f:
            f.write(csv_content)
        await client.send_document(
            chat_id=callback_query.from_user.id,
            document="status_export.csv",
            caption="📄 **Status Export (CSV)**",
            parse_mode="markdown"
        )
        await callback_query.answer("Status exported!")
    elif data == "noop":
        await callback_query.answer()
    await callback_query.answer()

if __name__ == "__main__":
    print("Starting Userbot Member Manager...")
    app.run()