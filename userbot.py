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

# ========= CLIENT =========
def get_client():
    global client_index
    i = client_index % len(clients)
    client_index += 1
    return i, clients[i]

# ========= BUTTON =========
async def click_button(msg, keywords):
    if not msg.buttons:
        return False
    for row in msg.buttons:
        for btn in row:
            if any(k in (btn.text or "").lower() for k in keywords):
                await msg.click(text=btn.text)
                return True
    return False

# ========= WAIT TEXT =========
async def wait_for_final_text(client, msg_id):
    for _ in range(40):  # 20 sec wait
        msg = await client.get_messages(SOURCE, ids=msg_id)
        text = msg.text or ""

        if "Email:" in text and "Password:" in text:
            return text

        await asyncio.sleep(0.5)

    return None

# ========= PARSER =========
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

# ========= FETCH =========
async def fetch_task(user_id):
    idx, client = get_client()

    async with locks[idx]:
        try:
            print("[USERBOT] Sending request...")
            await client.send_message(SOURCE, "➕ Register a new account")
            await asyncio.sleep(2)

            msg = (await client.get_messages(SOURCE, limit=1))[0]
            msg_id = msg.id

            # click flow
            await asyncio.sleep(1)
            await click_button(msg, ["done"])
            await asyncio.sleep(1)

            msg = await client.get_messages(SOURCE, ids=msg_id)
            await click_button(msg, ["complete"])
            await asyncio.sleep(1)

            msg = await client.get_messages(SOURCE, ids=msg_id)
            await click_button(msg, ["confirm"])

            # wait final text
            text = await wait_for_final_text(client, msg_id)

            if not text:
                print("[USERBOT] ❌ No final text")
                return

            print("[USERBOT] ✅ Text received")

            first, last, email, password, recovery = parse_task(text)

            if not email or not password:
                print("[USERBOT] ❌ Parse failed")
                return

            task_id = f"{user_id}_{msg_id}"

            con = db()
            cur = con.cursor()

            cur.execute("""
            INSERT INTO tasks(
                user_id, task_text,
                first_name, last_name, email, password, recovery_email,
                task_id, msg_id, status, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                user_id,
                text,
                first,
                last,
                email,
                password,
                recovery,
                task_id,
                msg_id,
                "done",
                int(time.time())
            ))

            con.commit()
            con.close()

            print("[USERBOT] 💾 Saved to DB")

        except Exception as e:
            print("[USERBOT ERROR]", e)

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
            print("[JOB ERROR]", e)

# ========= START =========
async def main():
    for i, c in enumerate(clients):
        await c.connect()
        print(f"[USERBOT] ✅ Client {i} ready")

    asyncio.create_task(job_loop())
    print("[USERBOT] 🚀 Running...")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
