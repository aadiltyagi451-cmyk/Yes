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
    (os.getenv("SESSION1") or "").strip(),
]

clients = [TelegramClient(StringSession(s), api_id, api_hash) for s in SESSION_STRINGS if s]
locks = [asyncio.Lock() for _ in clients]

# ========= DB =========
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_type TEXT,
        status TEXT DEFAULT 'pending'
    )
    """)

    con.commit()
    con.close()

# ========= PARSER =========
def parse_task(text):
    first = re.search(r'First name:\s*([^\n]+)', text)
    last = re.search(r'Last name:\s*([^\n]+)', text)
    email = re.search(r'Email:\s*([^\n]+)', text)
    password = re.search(r'Password:\s*([^\n]+)', text)

    recovery = re.search(r'([a-zA-Z0-9._%+-]+@gmail\.com)', text)

    first = first.group(1).strip() if first else ""
    last = last.group(1).strip() if last else ""
    email = email.group(1).strip() if email else ""
    password = password.group(1).strip() if password else ""
    recovery = recovery.group(1).strip() if recovery else "Not Provided"

    if last == "✖️":
        last = ""

    return first, last, email, password, recovery

# ========= BUTTON HELPERS =========
async def click_button(msg, keywords):
    if not msg.buttons:
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
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if any(k in (btn.text or "").lower() for k in keywords):
                        return msg
        await asyncio.sleep(0.5)
    return None

# ========= CLIENT =========
def get_client():
    return clients[0] if clients else None

# ========= FETCH =========
async def fetch_task(user_id):
    client = get_client()
    if client is None:
        return

    async with locks[0]:

        await client.send_message(SOURCE, "➕ Register a new account")
        await asyncio.sleep(1)

        msgs = await client.get_messages(SOURCE, limit=1)
        if not msgs:
            return

        msg = msgs[0]
        msg_id = msg.id

        # 🔥 STEP 1: DONE
        msg = await wait_for_button(client, msg_id, ["done"])
        if not msg: return
        await click_button(msg, ["done"])

        # 🔥 STEP 2: COMPLETE
        msg = await wait_for_button(client, msg_id, ["complete"])
        if not msg: return
        await click_button(msg, ["complete"])

        # 🔥 STEP 3: CONFIRM
        msg = await wait_for_button(client, msg_id, ["confirm"])
        if not msg: return
        await click_button(msg, ["confirm"])

        await asyncio.sleep(1)

        final = await client.get_messages(SOURCE, ids=msg_id)
        text = final.text or ""

        first, last, email, password, recovery = parse_task(text)
        name = (first + " " + last).strip()

        con = db()
        cur = con.cursor()

        now = int(time.time())

        cur.execute("""
        INSERT INTO registrations(
            user_id, first_name, email, password, recovery_email,
            created_at, state, status, task_id, msg_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            int(user_id),
            name,
            email,
            password,
            recovery,
            now,
            "created",
            "fetched",
            f"{user_id}_{msg_id}",
            int(msg_id)
        ))

        reg_id = cur.lastrowid

        cur.execute("""
        INSERT INTO actions(
            user_id, reg_id, created_at, expires_at, state
        ) VALUES(?,?,?,?,?)
        """, (
            int(user_id),
            reg_id,
            now,
            now + 20 * 3600,
            "shown"
        ))

        con.commit()
        con.close()

        print(f"✅ FETCH DONE {user_id}")

# ========= CONFIRM =========
async def confirm_task(user_id):
    client = get_client()
    if client is None:
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT id, msg_id FROM registrations
    WHERE user_id=?
    ORDER BY id DESC LIMIT 1
    """, (user_id,))

    row = cur.fetchone()
    con.close()

    if not row:
        return

    reg_id = row["id"]
    msg_id = row["msg_id"]

    async with locks[0]:

        msg = await client.get_messages(SOURCE, ids=msg_id)
        await click_button(msg, ["done", "✓"])

        for _ in range(30):
            await asyncio.sleep(1)

            updated = await client.get_messages(SOURCE, ids=msg_id)
            text = (updated.text or "").lower()

            if "done" in text:
                update_status(reg_id, "success")
                return

            if "try again" in text:
                update_status(reg_id, "fail")
                return

# ========= UPDATE =========
def update_status(reg_id, status):
    con = db()
    cur = con.cursor()

    cur.execute("""
    UPDATE registrations
    SET status=?
    WHERE id=?
    """, (status, reg_id))

    con.commit()
    con.close()

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

        cur.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
        con.commit()
        con.close()

        try:
            if job["job_type"] == "fetch":
                await fetch_task(job["user_id"])

            elif job["job_type"] == "confirm":
                await confirm_task(job["user_id"])

        except Exception as e:
            print("❌ ERROR:", e)

# ========= START =========
async def main():
    init_db()

    for c in clients:
        await c.start()
        print("✅ Userbot connected")

    asyncio.create_task(job_loop())
    print("🚀 Userbot running")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
