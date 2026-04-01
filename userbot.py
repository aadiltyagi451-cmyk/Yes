import os
import re
import time
import asyncio
import sqlite3
from telethon import TelegramClient
from telethon.sessions import StringSession

# ========= CONFIG =========
api_id = 36180474
api_hash = "1f4ecc2133837a8a3c307f676cb95f88"

SOURCE = "@GmailFarmerBot"

SESSION_STRINGS = [
    os.getenv("SESSION1"),
    os.getenv("SESSION2"),
]

clients = [TelegramClient(StringSession(s), api_id, api_hash) for s in SESSION_STRINGS if s]

if not clients:
    raise RuntimeError("No sessions loaded")

locks = [asyncio.Lock() for _ in clients]
client_index = 0

# ========= DB =========
def db():
    return sqlite3.connect("userbot.db")

# ========= HELPERS =========
def get_client():
    global client_index
    i = client_index % len(clients)
    client_index += 1
    return i, clients[i]

async def click_button(msg, keywords):
    if not msg.buttons:
        return False

    for row in msg.buttons:
        for btn in row:
            txt = (btn.text or "").lower()
            for k in keywords:
                if k in txt:
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

# ========= FETCH TASK =========
async def fetch_task(user_id):
    idx, client = get_client()

    async with locks[idx]:

        await client.send_message(SOURCE, "➕ Register a new account")
        await asyncio.sleep(1)

        msg = (await client.get_messages(SOURCE, limit=1))[0]
        msg_id = msg.id

        # 🔥 STEP FLOW
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

        task_id = f"{user_id}_{msg_id}"

        # 🔥 SAVE DB
        con = db()
        cur = con.cursor()

        cur.execute("""
        INSERT INTO tasks(user_id, task_text, task_id, msg_id, status, created_at)
        VALUES(?,?,?,?,?,?)
        """, (
            user_id,
            text,
            task_id,
            msg_id,
            "fetched",
            int(time.time())
        ))

        con.commit()
        con.close()

# ========= CONFIRM =========
async def confirm_task(user_id):
    con = db()
    cur = con.cursor()

    cur.execute("""
    SELECT task_id, msg_id FROM tasks
    WHERE user_id=? ORDER BY id DESC LIMIT 1
    """, (user_id,))

    row = cur.fetchone()
    con.close()

    if not row:
        return

    task_id, msg_id = row

    idx, client = get_client()

    async with locks[idx]:

        msg = await client.get_messages(SOURCE, ids=msg_id)

        # 🔥 CLICK DONE AGAIN
        if not await click_button(msg, ["done", "✓"]):
            await click_button(msg, ["check"])

        # 🔥 WAIT RESULT
        for _ in range(30):
            await asyncio.sleep(1)

            updated = await client.get_messages(SOURCE, ids=msg_id)
            text = (updated.text or "").lower()

            # ✅ SUCCESS
            if "how to logout" in text or "done" in text:
                save_result(user_id, task_id, "success")
                return

            # ❌ FAIL
            if "try again" in text or "not done" in text:
                save_result(user_id, task_id, "fail")
                return

def save_result(user_id, task_id, status):
    con = db()
    cur = con.cursor()

    cur.execute("""
    INSERT INTO results(user_id, task_id, status, created_at)
    VALUES(?,?,?,?)
    """, (user_id, task_id, status, int(time.time())))

    con.commit()
    con.close()

# ========= MANUAL TRIGGER (IMPORTANT) =========
# 👇 main bot se call karoge

async def handle_job(job):
    if job["type"] == "fetch":
        await fetch_task(job["user"])

    elif job["type"] == "confirm":
        await confirm_task(job["user"])

# ========= MAIN =========
async def start_userbot():
    for i, c in enumerate(clients):
        await c.start()
        print("CLIENT READY:", i)

    # background loop (non-blocking)
    asyncio.create_task(_keep_alive())


async def _keep_alive():
    while True:
        await asyncio.sleep(5)
