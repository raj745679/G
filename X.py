# Xx.py
import asyncio
import logging
import sqlite3
from datetime import datetime
import pytz
from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelInvalid, ChannelPrivate, UsernameNotOccupied, PeerIdInvalid
from pyrogram.types import ChatPrivileges
import random
import os
import sys

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_progress_bar(progress, total, width=20):
    """Progress bar generator"""
    if total == 0:
        return "─" * width
    filled = int(width * min(progress, total) / total)
    return "█" * filled + "─" * (width - filled)

class MemberTransfer:
    def __init__(self):
        self.setup_database()
        
    def setup_database(self):
        """Initialize database"""
        self.conn = sqlite3.connect('member_transfer.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transfer_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_group_id TEXT,
                target_group_id TEXT,
                total_members INTEGER,
                transferred_members INTEGER,
                status TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        ''')
        self.conn.commit()

    async def debug_groups(self, app):
        """Debug function to list all accessible groups"""
        print("\n🔍 Scanning your accessible groups...")
        groups_found = []
        
        async for dialog in app.get_dialogs():
            chat = dialog.chat
            if chat.type in ["group", "supergroup"]:
                group_info = {
                    'id': chat.id,
                    'title': chat.title,
                    'username': getattr(chat, 'username', 'No Username'),
                    'type': chat.type
                }
                groups_found.append(group_info)
                print(f"   💬 {chat.title}")
                print(f"      📝 ID: {chat.id}")
                print(f"      🔗 Username: @{getattr(chat, 'username', 'No Username')}")
                print(f"      🏷️ Type: {chat.type}")
                
                # Check admin status in this group
                try:
                    my_status = await app.get_chat_member(chat.id, "me")
                    admin_status = "✅ ADMIN" if my_status.status in ["creator", "administrator"] else "❌ NOT ADMIN"
                    print(f"      🛡️ Status: {admin_status} ({my_status.status})")
                except Exception as e:
                    print(f"      🛡️ Status: ❌ Error checking admin: {e}")
                
                print("      " + "─" * 40)
        
        print(f"\n✅ Total groups found: {len(groups_found)}")
        return groups_found

    async def resolve_group_identifier(self, app, identifier):
        """Resolve group identifier (ID or username) to chat object and ID"""
        try:
            if isinstance(identifier, int):
                chat = await app.get_chat(identifier)
                return True, chat, chat.id
            else:
                # Assume username (with or without @)
                username = identifier.lstrip('@')
                peer = await app.resolve_peer(f"@{username}")
                chat = await app.get_chat(peer.chat_id)
                return True, chat, chat.id
        except ChannelInvalid:
            print(f"❌ ChannelInvalid: Cannot access group {identifier}")
            print("   💡 Make sure:")
            print("      - You are a member of this group")
            print("      - The group ID or username is correct")
            print("      - The group exists")
            return False, None, None
        except ChannelPrivate:
            print(f"❌ ChannelPrivate: Group {identifier} is private and you're not a member")
            print("   💡 Join the group first with your account")
            return False, None, None
        except UsernameNotOccupied:
            print(f"❌ UsernameNotOccupied: Group {identifier} doesn't exist")
            return False, None, None
        except PeerIdInvalid:
            print(f"❌ PeerIdInvalid: Invalid peer ID or username {identifier}")
            print("   💡 Ensure the ID/username is correct and you've interacted with the group (e.g., joined it or messaged in it).")
            print("   💡 If it's a private group, join it first. For unknown peers, try using the username instead of ID.")
            return False, None, None
        except Exception as e:
            print(f"❌ Error accessing group {identifier}: {e}")
            return False, None, None

    async def verify_group_access(self, app, chat_id):
        """Verify if we can access the group and get member count"""
        try:
            chat = await app.get_chat(chat_id)
            print(f"✅ Group Found: {chat.title}")
            print(f"   📝 ID: {chat.id}")
            print(f"   🔗 Username: @{getattr(chat, 'username', 'No Username')}")
            print(f"   🏷️ Type: {chat.type}")
            
            # Try to get member count
            try:
                members_count = await app.get_chat_members_count(chat.id)
                print(f"   👥 Members Count: {members_count}")
                return True, chat, members_count
            except:
                print(f"   ⚠️ Cannot get member count (may need admin rights)")
                return True, chat, 0
                
        except Exception as e:
            print(f"❌ Error verifying group access {chat_id}: {e}")
            return False, None, 0

    async def check_admin_rights(self, app, chat_id):
        """Check if user has admin rights to add members"""
        try:
            my_status = await app.get_chat_member(chat_id, "me")
            print(f"   🛡️ Your role: {my_status.status}")
            
            if my_status.status in ["creator", "administrator"]:
                if my_status.status == "creator":
                    print("   ✅ Creator privileges - Full permissions")
                    return True
                elif my_status.status == "administrator":
                    if my_status.privileges and my_status.privileges.can_invite_users:
                        print("   ✅ Admin with invite permissions")
                        return True
                    else:
                        print("   ❌ Admin but missing 'Invite Users' permission")
                        print("   💡 Ask group owner to grant you 'Invite Users' permission")
                        return False
            else:
                print("   ❌ You are not admin in this group")
                print("   💡 You need to be admin with 'Invite Users' permission")
                return False
                
        except Exception as e:
            print(f"   ❌ Error checking admin rights: {e}")
            return False

    async def transfer_members(self, source_identifier, target_identifier):
        """Main function to transfer members from source to target"""
        api_id = int(os.getenv("API_ID"))
        api_hash = os.getenv("API_HASH")
        session_name = "user_account"
        
        async with Client(session_name, api_id=api_id, api_hash=api_hash) as app:
            try:
                print(f"🚀 Starting member transfer...")
                print(f"📥 Source: {source_identifier}")
                print(f"📤 Target: {target_identifier}")
                print("─" * 50)

                # Get current user info
                me = await app.get_me()
                print(f"👤 Logged in as: {me.first_name} (@{me.username})")
                print(f"📱 Phone: {me.phone_number}")
                print("─" * 50)

                # Debug: Show all accessible groups
                await self.debug_groups(app)
                print("─" * 50)

                # Resolve source group
                print("🔍 Resolving source group...")
                source_accessible, source_chat, source_chat_id = await self.resolve_group_identifier(app, source_identifier)
                
                if not source_accessible:
                    print("❌ Cannot access source group. Please fix the issue above.")
                    return

                # Verify source group access
                print("\n🔍 Verifying source group access...")
                source_accessible, source_chat, source_member_count = await self.verify_group_access(app, source_chat_id)
                
                if not source_accessible:
                    print("❌ Cannot verify source group access. Please fix the issue above.")
                    return

                # Resolve target group
                print("\n🔍 Resolving target group...")
                target_accessible, target_chat, target_chat_id = await self.resolve_group_identifier(app, target_identifier)
                
                if not target_accessible:
                    print("❌ Cannot access target group. Please fix the issue above.")
                    return

                # Verify target group access
                print("\n🔍 Verifying target group access...")
                target_accessible, target_chat, target_member_count = await self.verify_group_access(app, target_chat_id)
                
                if not target_accessible:
                    print("❌ Cannot verify target group access. Please fix the issue above.")
                    return

                # Check admin rights in target group
                print("\n🔍 Checking admin rights in target group...")
                has_admin_rights = await self.check_admin_rights(app, target_chat_id)
                
                if not has_admin_rights:
                    print("❌ You don't have sufficient admin rights in target group!")
                    return

                # Get members from source group
                print(f"\n📥 Collecting members from source group...")
                members = []
                total_count = 0
                
                try:
                    async for member in app.get_chat_members(source_chat_id):
                        user = member.user
                        if not user.is_bot and not user.is_deleted and not user.is_self:
                            user_info = {
                                'id': user.id,
                                'username': user.username,
                                'first_name': user.first_name,
                                'last_name': user.last_name
                            }
                            members.append(user_info)
                            total_count += 1
                            
                            if total_count % 50 == 0:
                                progress = get_progress_bar(total_count, 1000)
                                print(f"👥 Collected {total_count} members... {progress}")
                    
                    print(f"✅ Total members collected: {total_count}")

                    if total_count == 0:
                        print("❌ No members found in source group")
                        return

                except Exception as e:
                    print(f"❌ Error collecting members: {e}")
                    print("💡 You may need to be admin in the source group to see members")
                    return

                # Start transfer session in database
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO transfer_sessions (source_group_id, target_group_id, total_members, transferred_members, status) VALUES (?, ?, ?, ?, ?)",
                    (str(source_identifier), str(target_identifier), total_count, 0, 'started')
                )
                session_id = cursor.lastrowid
                self.conn.commit()

                # Ask for confirmation
                print(f"\n⚠️ READY TO START TRANSFER:")
                print(f"   Source: {source_chat.title} ({total_count} members)")
                print(f"   Target: {target_chat.title}")
                print(f"   Estimated time: {total_count * 6 / 60:.1f} minutes")
                
                confirm = input("\n❓ Continue with transfer? (y/N): ").strip().lower()
                if confirm not in ['y', 'yes']:
                    print("🚫 Transfer cancelled by user")
                    return

                # Transfer members
                print("📤 Starting member transfer...")
                success_count = 0
                failed_count = 0
                failed_users = []

                for index, user_info in enumerate(members):
                    user_id = user_info['id']
                    try:
                        await app.add_chat_members(target_chat_id, user_id)
                        success_count += 1
                        
                        full_name = f"{user_info['first_name']} {user_info['last_name'] or ''}".strip()
                        username = f"@{user_info['username']}" if user_info['username'] else "no_username"
                        print(f"✅ [{success_count}] Added: {full_name} ({username})")
                        
                        if (index + 1) % 5 == 0 or (index + 1) == total_count:
                            progress = get_progress_bar(success_count, total_count)
                            percentage = (success_count / total_count) * 100
                            print(f"🔄 Overall Progress: {success_count}/{total_count} ({percentage:.1f}%) {progress}")
                            
                            cursor.execute(
                                "UPDATE transfer_sessions SET transferred_members = ? WHERE id = ?",
                                (success_count, session_id)
                            )
                            self.conn.commit()

                        delay = random.uniform(5, 8)
                        await asyncio.sleep(delay)

                    except FloodWait as e:
                        print(f"⏳ Flood wait: {e.value} seconds...")
                        wait_time = e.value
                        for i in range(wait_time, 0, -1):
                            if i % 30 == 0 or i <= 10:
                                print(f"⏰ Waiting: {i} seconds remaining...")
                            await asyncio.sleep(1)
                        print("🔄 Resuming transfer after flood wait...")
                        continue
                        
                    except Exception as e:
                        failed_count += 1
                        failed_users.append(user_info)
                        error_msg = str(e)
                        
                        full_name = f"{user_info['first_name']} {user_info['last_name'] or ''}".strip()
                        username = f"@{user_info['username']}" if user_info['username'] else "no_username"
                        
                        print(f"❌ Failed to add {full_name}: {error_msg}")
                        
                        if "privacy" in error_msg.lower():
                            print(f"   ⚠️ User privacy settings prevent adding")
                        elif "bot" in error_msg.lower():
                            print(f"   ⚠️ Cannot add bots")
                        elif "kicked" in error_msg.lower():
                            print(f"   ⚠️ User is banned from target group")
                        elif "user not found" in error_msg.lower():
                            print(f"   ⚠️ User account not found")
                        
                        logger.error(f"Failed to add user {user_id}: {e}")
                        continue

                # Complete session
                cursor.execute(
                    "UPDATE transfer_sessions SET status = ?, transferred_members = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    ('completed', success_count, session_id)
                )
                self.conn.commit()

                print(f"\n🎉 Transfer Completed!")
                print(f"✅ Successfully added: {success_count} members")
                print(f"❌ Failed: {failed_count} members")
                if total_count > 0:
                    print(f"📊 Success rate: {(success_count/total_count)*100:.1f}%")
                
                if failed_users:
                    print(f"\n📝 Failed users (first 10):")
                    for user in failed_users[:10]:
                        full_name = f"{user['first_name']} {user['last_name'] or ''}".strip()
                        username = f"@{user['username']}" if user['username'] else "no_username"
                        print(f"   ❌ {full_name} ({username})")

            except Exception as e:
                logger.error(f"Transfer failed: {e}")
                print(f"❌ Transfer failed: {e}")
                try:
                    cursor = self.conn.cursor()
                    cursor.execute(
                        "UPDATE transfer_sessions SET status = ? WHERE id = ?",
                        ('failed', session_id)
                    )
                    self.conn.commit()
                except:
                    pass

    async def show_history(self):
        """Show transfer history"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, source_group_id, target_group_id, total_members, 
                   transferred_members, status, started_at, completed_at 
            FROM transfer_sessions 
            ORDER BY started_at DESC 
            LIMIT 10
        ''')
        sessions = cursor.fetchall()
        
        if not sessions:
            print("📭 No transfer history found")
            return
        
        print("\n📋 Transfer History:")
        print("=" * 80)
        print(f"{'ID':<3} | {'Source':<20} | {'Target':<20} | {'Transferred':<12} | {'Status':<10} | {'Started':<16}")
        print("-" * 80)
        
        for session in sessions:
            session_id, source_id, target_id, total, transferred, status, started, completed = session
            status_icon = "✅" if status == "completed" else "🔄" if status == "started" else "❌"
            started_str = started[:16] if started else "N/A"
            
            print(f"{session_id:<3} | {source_id:<20} | {target_id:<20} | {transferred}/{total:<10} | {status_icon:<10} | {started_str:<16}")
        
        print("=" * 80)

def print_banner():
    """Print application banner"""
    banner = """
╔════════════════════════════════════════╗
║        🚀 MEMBER TRANSFER TOOL        ║
║           Direct Group to Group        ║
╚════════════════════════════════════════╝
    """
    print(banner)

def print_usage():
    """Print usage instructions"""
    print("\n📖 Usage:")
    print("  python3 Xx.py transfer <source_id_or_username> <target_id_or_username>")
    print("  python3 Xx.py history")
    print("  python3 Xx.py debug")
    print("\n📝 Examples:")
    print("  python3 Xx.py transfer -100123456789 -100987654321")
    print("  python3 Xx.py transfer @SourceGroup @TargetGroup")
    print("  python3 Xx.py history")
    print("  python3 Xx.py debug")
    print("\n🔧 Setup:")
    print("  export API_ID=your_api_id")
    print("  export API_HASH=your_api_hash")
    print("\n💡 Notes:")
    print("  - Use group IDs (e.g., -100123456789) or usernames (e.g., @GroupUsername)")
    print("  - For private groups, ensure you're a member and have interacted with the group")
    print("  - Run 'debug' to list accessible groups and verify IDs")
    print("  - Get API credentials from https://my.telegram.org/")

async def debug_groups():
    """Debug function to list all accessible groups"""
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    
    async with Client("user_account", api_id=api_id, api_hash=api_hash) as app:
        transfer_tool = MemberTransfer()
        await transfer_tool.debug_groups(app)

def parse_group_identifier(identifier: str):
    """Parse group identifier to determine if it's an ID or username"""
    if identifier.startswith('@'):
        return identifier[1:]  # Strip @ for username
    elif identifier.startswith('-') and identifier[1:].isdigit():
        return int(identifier)  # Convert to int for ID
    else:
        return identifier  # Assume username

async def main():
    """Main function"""
    print_banner()
    
    if not os.getenv("API_ID") or not os.getenv("API_HASH"):
        print("❌ Please set environment variables first:")
        print("export API_ID=your_api_id")
        print("export API_HASH=your_api_hash")
        print("\n💡 Get these from https://my.telegram.org/")
        return
    
    if len(sys.argv) < 2:
        print_usage()
        return
    
    command = sys.argv[1]
    transfer_tool = MemberTransfer()
    
    if command == "transfer":
        if len(sys.argv) != 4:
            print("❌ Usage: python3 Xx.py transfer <source_id_or_username> <target_id_or_username>")
            return
        
        source_input = sys.argv[2]
        target_input = sys.argv[3]
        
        source_identifier = parse_group_identifier(source_input)
        target_identifier = parse_group_identifier(target_input)
        
        await transfer_tool.transfer_members(source_identifier, target_identifier)
        
    elif command == "history":
        await transfer_tool.show_history()
        
    elif command == "debug":
        await debug_groups()
        
    else:
        print("❌ Unknown command")
        print_usage()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Program interrupted by user")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")