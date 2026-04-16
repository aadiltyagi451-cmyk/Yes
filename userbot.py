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
CLIENT_STATE = {}     # idx -> tracking msg
CLICKED = set()       # prevent spam click

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
        msg_id INTEGER,
        created_at INTEGER,
        state TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_type TEXT,
        payload TEXT,
        status TEXT,
        created_at INTEGER
    )
    """)

    con.commit()
    con.close()

# ========= CLIENT =========
def get_client():
    global client_index
    i = client_index % len(clients)
    client_index += 1
    return i, clients[i]

# ========= PARSER =========
def parse_task(text):
    email = re.search(r'Email:\s*([^\n]+)', text)
    password = re.search(r'Password:\s*([^\n]+)', text)
    first = re.search(r'First name:\s*([^\n]+)', text)
    last = re.search(r'Last name:\s*([^\n]+)', text)
    recovery = re.search(r'Recovery email\s*([^\s\n]+)', text, re.I)

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

    text = (msg.text or "").lower()
    msg_id = msg.id

    for idx, state in list(CLIENT_STATE.items()):

        if msg_id != state["msg_id"]:
            continue

        if not msg.buttons:
            continue

        for row in msg.buttons:
            for btn in row:
                t = (btn.text or "").lower()

                key = f"{msg_id}_{btn.text}"

                if key in CLICKED:
                    continue

                try:
                    # STEP FLOW FIXED (ONLY ONCE)
                    if "done" in t:
                        await msg.click(text=btn.text)
                        CLICKED.add(key)
                        print("[AUTO] DONE CLICK")
                        return

                    if "complete" in t:
                        await msg.click(text=btn.text)
                        CLICKED.add(key)
                        print("[AUTO] COMPLETE CLICK")
                        return

                    if "confirm" in t:
                        await msg.click(text=btn.text)
                        CLICKED.add(key)
                        print("[AUTO] CONFIRM CLICK")
                        return

                except Exception as e:
                    print("[CLICK ERROR]", e)

        # ========= FETCH RESULT =========
        if "email" in text and "password" in text:

            user_id = state["user_id"]

            first, last, email, password, recovery = parse_task(msg.text or "")

            con = db()
            cur = con.cursor()

            cur.execute("""
            INSERT INTO registrations(
                user_id, first_name, last_name, email, password,
                recovery_email, msg_id, created_at, state
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                user_id, first, last, email, password,
                recovery, msg_id, int(time.time()), "fetched"
            ))

            con.commit()
            con.close()

            print("[FETCH] SAVED")

            CLIENT_STATE.pop(idx, None)
            CLICKED.clear()
            return

# ========= FETCH =========
async def fetch_task(user_id):
    idx, client = get_client()

    async with locks[idx]:
        await client.send_message(SOURCE, "➕ Register a new Gmail")
        await asyncio.sleep(2)

        msg = (await client.get_messages(SOURCE, limit=1))[0]

        CLIENT_STATE[idx] = {
            "user_id": user_id,
            "msg_id": msg.id
        }

        print("[TRACK]", msg.id)

# ========= CONFIRM TASK (FINAL STEP ONLY) =========
async def confirm_task(user_id, msg_id):
    idx, client = get_client()

    async with locks[idx]:

        msg = await client.get_messages(SOURCE, ids=int(msg_id))
        if not msg:
            return

        # FINAL DONE ONLY HERE
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if "done" in (btn.text or "").lower():
                        await msg.click(text=btn.text)
                        print("[CONFIRM] FINAL DONE CLICKED")
                        break

        success = False

        for _ in range(30):
            msg = await client.get_messages(SOURCE, ids=int(msg_id))
            text = (msg.text or "").lower()

            if "logout" in text:
                success = True
                break

            await asyncio.sleep(0.5)

        con = db()
        cur = con.cursor()

        state_val = "done" if success else "failed"

        cur.execute("UPDATE registrations SET state=? WHERE msg_id=?", (state_val, msg_id))
        con.commit()
        con.close()

        CLIENT_STATE.pop(idx, None)
        CLICKED.clear()

        print("[CONFIRM] RESULT:", state_val)

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
            print("[READY]", i)

    asyncio.create_task(job_loop())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
