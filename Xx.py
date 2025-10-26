# X.py
import asyncio
import logging
import sqlite3
from datetime import datetime
import pytz
from pyrogram import Client
from pyrogram.errors import FloodWait
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
                source_group_id INTEGER,
                target_group_id INTEGER,
                total_members INTEGER,
                transferred_members INTEGER,
                status TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        ''')
        self.conn.commit()

    async def transfer_members(self, source_group_id, target_group_id):
        """Main function to transfer members from source to target"""
        
        # Get client from environment variables
        api_id = int(os.getenv("API_ID"))
        api_hash = os.getenv("API_HASH")
        
        async with Client("member_transfer", api_id=api_id, api_hash=api_hash) as app:
            try:
                print(f"🚀 Starting member transfer...")
                print(f"📥 Source Group: {source_group_id}")
                print(f"📤 Target Group: {target_group_id}")
                
                # Verify source group
                try:
                    source_chat = await app.get_chat(source_group_id)
                    print(f"✅ Source: {source_chat.title}")
                except Exception as e:
                    print(f"❌ Cannot access source group: {e}")
                    return

                # Verify target group and check admin rights
                try:
                    target_chat = await app.get_chat(target_group_id)
                    print(f"✅ Target: {target_chat.title}")
                    
                    # Check if bot is admin in target group
                    target_member = await app.get_chat_member(target_group_id, "me")
                    if target_member.status not in ["creator", "administrator"]:
                        print("❌ I must be admin in the target group!")
                        return
                        
                except Exception as e:
                    print(f"❌ Cannot access target group: {e}")
                    return

                # Get members from source group
                print("📥 Collecting members from source group...")
                members = []
                total_count = 0
                
                async for member in app.get_chat_members(source_group_id):
                    user = member.user
                    if not user.is_bot and not user.is_deleted:
                        members.append(user.id)
                        total_count += 1
                        
                        if total_count % 50 == 0:
                            progress = get_progress_bar(total_count, 1000)
                            print(f"👥 Collected {total_count} members... {progress}")
                
                print(f"✅ Total members collected: {total_count}")

                if total_count == 0:
                    print("❌ No members found in source group")
                    return

                # Start transfer session in database
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO transfer_sessions (source_group_id, target_group_id, total_members, transferred_members, status) VALUES (?, ?, ?, ?, ?)",
                    (source_group_id, target_group_id, total_count, 0, 'started')
                )
                session_id = cursor.lastrowid
                self.conn.commit()

                # Transfer members to target group
                print("📤 Starting member transfer...")
                success_count = 0
                failed_count = 0

                for index, user_id in enumerate(members):
                    try:
                        await app.add_chat_members(target_group_id, user_id)
                        success_count += 1
                        
                        # Update progress every 5 members or at the end
                        if (index + 1) % 5 == 0 or (index + 1) == total_count:
                            progress = get_progress_bar(success_count, total_count)
                            print(f"🔄 Progress: {success_count}/{total_count} {progress}")
                            
                            # Update database
                            cursor.execute(
                                "UPDATE transfer_sessions SET transferred_members = ? WHERE id = ?",
                                (success_count, session_id)
                            )
                            self.conn.commit()

                        # Random delay to avoid flood
                        delay = random.uniform(3, 6)
                        await asyncio.sleep(delay)

                    except FloodWait as e:
                        print(f"⏳ Flood wait: {e.value} seconds...")
                        for i in range(e.value, 0, -1):
                            print(f"⏰ Waiting: {i} seconds remaining...")
                            await asyncio.sleep(1)
                        continue
                        
                    except Exception as e:
                        failed_count += 1
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
                print(f"📊 Success rate: {(success_count/total_count)*100:.1f}%")

            except Exception as e:
                logger.error(f"Transfer failed: {e}")
                print(f"❌ Transfer failed: {e}")

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
        print(f"{'ID':<3} | {'Source':<12} | {'Target':<12} | {'Transferred':<12} | {'Status':<10} | {'Started':<16}")
        print("-" * 80)
        
        for session in sessions:
            session_id, source_id, target_id, total, transferred, status, started, completed = session
            status_icon = "✅" if status == "completed" else "🔄" if status == "started" else "❌"
            started_str = started[:16] if started else "N/A"
            
            print(f"{session_id:<3} | {source_id:<12} | {target_id:<12} | {transferred}/{total:<10} | {status_icon:<10} | {started_str:<16}")
        
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
    print("  python3 X.py transfer <source_id> <target_id>")
    print("  python3 X.py history")
    print("\n📝 Examples:")
    print("  python3 X.py transfer -100123456789 -100987654321")
    print("  python3 X.py history")
    print("\n🔧 Setup:")
    print("  export API_ID=your_api_id")
    print("  export API_HASH=your_api_hash")

async def main():
    """Main function"""
    print_banner()
    
    # Check environment variables
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
            print("❌ Usage: python3 X.py transfer <source_id> <target_id>")
            return
        
        try:
            source_id = int(sys.argv[2])
            target_id = int(sys.argv[3])
        except ValueError:
            print("❌ Group IDs must be integers")
            return
        
        await transfer_tool.transfer_members(source_id, target_id)
        
    elif command == "history":
        await transfer_tool.show_history()
        
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