import os
import time
import asyncio
import sqlite3
import re
from telethon import TelegramClient
from telethon.sessions import StringSession

# ========= CONFIG =========
api_id = 36180474
api_hash = "1f4ecc2133837a8a3c307f676cb95f88"
SOURCE = "@GmailFarmerBot"
DB_PATH = "bot.db"

SESSION_STRINGS = [
    (os.getenv("SESSION5") or "").strip(),
    (os.getenv("SESSION6") or "").strip(),
]
SESSION_STRINGS = [s for s in SESSION_STRINGS if s]

clients = []
locks = []

for s in SESSION_STRINGS:
    if s:
        clients.append(TelegramClient(StringSession(s), api_id, api_hash))
        locks.append(asyncio.Lock())

client_index = 0

# ========= DB =========
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()

    # 🔥 MAIN TABLE (IMPORTANT)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS registrations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        first_name TEXT,
        last_name TEXT,
        email TEXT,
        password TEXT,
        recovery_email TEXT,
        task_id TEXT,
        msg_id INTEGER,
        created_at INTEGER,
        state TEXT
    )
    """)

    # JOB QUEUE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_type TEXT NOT NULL,
        payload TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at INTEGER,
        updated_at INTEGER,
        error TEXT DEFAULT ''
    )
    """)

    # ✅ migration (INSIDE function)
    cur.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in cur.fetchall()}

    if "payload" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN payload TEXT DEFAULT ''")

    if "created_at" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN created_at INTEGER")

    if "updated_at" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN updated_at INTEGER")

    if "error" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN error TEXT DEFAULT ''")

    con.commit()
    con.close()

# ========= HELPERS =========
def get_client():
    global client_index
    if not clients:
        return None, None
    i = client_index % len(clients)
    client_index += 1
    return i, clients[i]

async def click_button(msg, keywords):
    if not getattr(msg, "buttons", None):
        return False
    for row in msg.buttons:
        for btn in row:
            if any(k in (btn.text or "").lower() for k in keywords):
                await msg.click(text=btn.text)
                return True
    return False

async def wait_for_button(client, msg_id, keywords, timeout=20):
    for _ in range(int(timeout * 2)):
        msg = await client.get_messages(SOURCE, ids=msg_id)
        if getattr(msg, "buttons", None):
            for row in msg.buttons:
                for btn in row:
                    if any(k in (btn.text or "").lower() for k in keywords):
                        return msg
        await asyncio.sleep(0.5)
    return None

# ========= PARSE =========
def parse_task(text):
    email = re.search(r'Email:\s*([^\n]+)', text)
    password = re.search(r'Password:\s*([^\n]+)', text)
    first = re.search(r'First name:\s*([^\n]+)', text)
    last = re.search(r'Last name:\s*([^\n]+)', text)
    recovery = re.search(r'Recovery email\s*([^\s\n]+@gmail\.com)', text, re.I)

    email = email.group(1).strip() if email else ""
    password = password.group(1).strip() if password else ""
    first = first.group(1).strip() if first else ""
    last = last.group(1).strip() if last else ""
    recovery = recovery.group(1).strip() if recovery else "Not Provided"

    if last == "✖️":
        last = ""

    return first, last, email, password, recovery

# ========= FETCH TASK =========
async def fetch_task(user_id):
    idx, client = get_client()
    if client is None:
        return

    async with locks[idx]:
        print("[USERBOT] 🔄 Fetching task...")

        await client.send_message(SOURCE, "➕ Register a new account")
        await asyncio.sleep(1)

        msgs = await client.get_messages(SOURCE, limit=1)
        if not msgs:
            return

        msg = msgs[0]
        msg_id = msg.id

        msg = await wait_for_button(client, msg_id, ["done"])
        if not msg: return
        await click_button(msg, ["done"])

        msg = await wait_for_button(client, msg_id, ["complete"])
        if not msg: return
        await click_button(msg, ["complete"])

        msg = await wait_for_button(client, msg_id, ["confirm"])
        if not msg: return
        await click_button(msg, ["confirm"])

        await asyncio.sleep(1)

        final = await client.get_messages(SOURCE, ids=msg_id)
        text = final.text or ""

        first, last, email, password, recovery = parse_task(text)

        task_id = f"{user_id}_{msg_id}"

        # 🔥 DIRECT SAVE TO registrations
        con = db()
        cur = con.cursor()

        cur.execute("""
        INSERT INTO registrations(
            user_id, first_name, last_name, email, password,
            recovery_email, task_id, msg_id, created_at, state
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            int(user_id),
            first,
            last,
            email,
            password,
            recovery,
            task_id,
            int(msg_id),
            int(time.time()),
            "fetched"
        ))

        con.commit()
        con.close()

        print("[USERBOT] ✅ Saved to registrations")

# ========= JOB LOOP =========
async def job_loop():
    while True:
        con = db()
        cur = con.cursor()

        cur.execute("SELECT * FROM jobs WHERE status='pending' LIMIT 1")
        job = cur.fetchone()

        if not job:
            con.close()
            await asyncio.sleep(1)
            continue

        cur.execute("UPDATE jobs SET status='processing' WHERE id=?", (job["id"],))
        con.commit()
        con.close()

        try:
            if job["job_type"] == "fetch":
                await fetch_task(job["user_id"])

            con = db()
            cur = con.cursor()
            cur.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
            con.commit()
            con.close()

        except Exception as e:
            print("[USERBOT] ❌", e)

# ========= START =========
async def main():
    init_db()

    for i, c in enumerate(clients):
        await c.connect()
        if await c.is_user_authorized():
            print(f"[USERBOT] ✅ Client {i} ready")

    print("[USERBOT] 🚀 Running...")
    asyncio.create_task(job_loop())

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
