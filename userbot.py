import os
import time
import asyncio
import sqlite3
import re
from telethon import TelegramClient, events
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
    clients.append(TelegramClient(StringSession(s), api_id, api_hash))
    locks.append(asyncio.Lock())

client_index = 0

# ========= GLOBAL =========
CLIENT_STATE = {}   # idx -> {user_id, msg_id}
FLOW_STATE = {}     # idx -> step (0-3)

# ========= DB =========
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()

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

    con.commit()
    con.close()

# ========= HELPERS =========
def get_client():
    global client_index
    i = client_index % len(clients)
    client_index += 1
    return i, clients[i]

# ========= PARSE =========
def parse_task(text):
    email = re.search(r'Email:\s*([^\n]+)', text)
    password = re.search(r'Password:\s*([^\n]+)', text)
    first = re.search(r'First name:\s*([^\n]+)', text)
    last = re.search(r'Last name:\s*([^\n]+)', text)
    recovery = re.search(r'Recovery email\s*([^\s\n]+@gmail\.com)', text, re.I)

    return (
        first.group(1).strip() if first else "",
        last.group(1).strip() if last else "",
        email.group(1).strip() if email else "",
        password.group(1).strip() if password else "",
        recovery.group(1).strip() if recovery else "Not Provided"
    )

# ========= AUTO HANDLER =========
async def auto_handler(event):
    msg = event.message
    if not msg:
        return

    msg_id = msg.id
    text = (msg.text or "").lower()

    for idx, state in list(CLIENT_STATE.items()):
        if msg_id != state["msg_id"]:
            continue

        step = FLOW_STATE.get(idx, 0)

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    t = (btn.text or "").lower()

                    try:
                        # STEP 1 → DONE
                        if step == 0 and "done" in t:
                            await msg.click(text=btn.text)
                            FLOW_STATE[idx] = 1
                            print("[FLOW] Done")
                            return

                        # STEP 2 → CONFIRM
                        elif step == 1 and "confirm" in t:
                            await msg.click(text=btn.text)
                            FLOW_STATE[idx] = 2
                            print("[FLOW] Confirm")
                            return

                        # STEP 3 → COMPLETE
                        elif step == 2 and "complete" in t:
                            await msg.click(text=btn.text)
                            FLOW_STATE[idx] = 3
                            print("[FLOW] Complete")
                            return

                    except Exception as e:
                        print("[CLICK ERROR]", e)

        # FINAL RESULT (FETCH)
        if "email" in text and "password" in text:
            user_id = state["user_id"]

            first, last, email, password, recovery = parse_task(msg.text or "")

            con = db()
            cur = con.cursor()

            cur.execute("""
            INSERT INTO registrations(
                user_id, first_name, last_name, email, password,
                recovery_email, task_id, msg_id, created_at, state
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                user_id, first, last, email, password,
                recovery, f"{user_id}_{msg_id}", msg_id, int(time.time()), "fetched"
            ))

            con.commit()
            con.close()

            print("[FETCH] ✅ SAVED")

            CLIENT_STATE.pop(idx, None)
            FLOW_STATE.pop(idx, None)
            return

# ========= FETCH =========
async def fetch_task(user_id):
    idx, client = get_client()

    async with locks[idx]:
        print("[FETCH] 🔄", user_id)

        await client.send_message(SOURCE, "➕ Register a new Gmail")
        await asyncio.sleep(1)

        msg = (await client.get_messages(SOURCE, limit=1))[0]

        CLIENT_STATE[idx] = {
            "user_id": user_id,
            "msg_id": msg.id
        }

        FLOW_STATE[idx] = 0

# ========= CONFIRM =========
async def confirm_task(user_id, msg_id):
    idx, client = get_client()

    async with locks[idx]:
        print("[CONFIRM] 🔄", user_id)

        CLIENT_STATE[idx] = {
            "user_id": user_id,
            "msg_id": int(msg_id)
        }

        FLOW_STATE[idx] = 3  # already reached complete

        msg = await client.get_messages(SOURCE, ids=int(msg_id))
        if not msg:
            return

        # 🔥 FINAL DONE CLICK (ONLY HERE)
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if "done" in (btn.text or "").lower():
                        await msg.click(text=btn.text)
                        print("[CONFIRM] ⚡ Final Done clicked")
                        break

        success = False

        for _ in range(30):
            msg = await client.get_messages(SOURCE, ids=int(msg_id))
            text = (msg.text or "").lower()

            if "how to logout of account" in text:
                success = True
                break

            await asyncio.sleep(0.5)

        con = db()
        cur = con.cursor()

        if success:
            cur.execute("UPDATE registrations SET state='done' WHERE user_id=?", (user_id,))
            print("[CONFIRM] ✅ SUCCESS")
        else:
            cur.execute("UPDATE registrations SET state='failed' WHERE user_id=?", (user_id,))
            print("[CONFIRM] ❌ FAILED")

        con.commit()
        con.close()

        CLIENT_STATE.pop(idx, None)
        FLOW_STATE.pop(idx, None)

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

            elif job["job_type"] == "confirm":
                await confirm_task(job["user_id"], job["payload"])

            con = db()
            cur = con.cursor()
            cur.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))
            con.commit()
            con.close()

        except Exception as e:
            print("[ERROR]", e)

# ========= START =========
async def main():
    init_db()

    for i, c in enumerate(clients):
        await c.connect()
        if await c.is_user_authorized():
            c.add_event_handler(auto_handler, events.NewMessage(from_users=SOURCE))
            c.add_event_handler(auto_handler, events.MessageEdited(from_users=SOURCE))
            print(f"[READY] Client {i}")

    asyncio.create_task(job_loop())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
