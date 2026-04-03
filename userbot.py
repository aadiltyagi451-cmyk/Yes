import os
import time
import json
import asyncio
import sqlite3
from telethon import TelegramClient
from telethon.sessions import StringSession

# ========= CONFIG =========
api_id = 36180474
api_hash = "1f4ecc2133837a8a3c307f676cb95f88"
SOURCE = "@GmailFarmerBot"
DB_PATH = "userbot.db"

SESSION_STRINGS = [
    (os.getenv("SESSION1") or "").strip(),
    (os.getenv("SESSION2") or "").strip(),
]
SESSION_STRINGS = [s for s in SESSION_STRINGS if s]

clients = []
locks = []

for s in SESSION_STRINGS:
    try:
        clients.append(TelegramClient(StringSession(s), api_id, api_hash))
        locks.append(asyncio.Lock())
    except Exception as e:
        print(f"[USERBOT] ⚠️ Failed to init client: {repr(e)}")

client_index = 0
_started = False
_job_task = None

# ========= DB =========
def db():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_text TEXT,
        task_id TEXT,
        msg_id INTEGER,
        status TEXT,
        created_at INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id TEXT,
        status TEXT,
        created_at INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_type TEXT NOT NULL,
        payload TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at INTEGER NOT NULL,
        updated_at INTEGER,
        error TEXT DEFAULT ''
    )
    """)
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
            txt = (btn.text or "").lower()
            if any(k in txt for k in keywords):
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

# ========= FETCH TASK =========
async def fetch_task(user_id):
    idx, client = get_client()
    if client is None:
        print("[USERBOT] ⚠️ No client available for fetch_task")
        return

    async with locks[idx]:
        await client.send_message(SOURCE, "➕ Register a new account")
        await asyncio.sleep(1)
        msgs = await client.get_messages(SOURCE, limit=1)
        if not msgs:
            return
        msg = msgs[0]
        msg_id = msg.id

        msg = await wait_for_button(client, msg_id, ["done"])
        if not msg:
            return
        await click_button(msg, ["done"])

        msg = await wait_for_button(client, msg_id, ["complete"])
        if not msg:
            return
        await click_button(msg, ["complete"])

        msg = await wait_for_button(client, msg_id, ["confirm"])
        if not msg:
            return
        await click_button(msg, ["confirm"])

        await asyncio.sleep(1)
        final = await client.get_messages(SOURCE, ids=msg_id)
        text = final.text or ""

        task_id = f"{user_id}_{msg_id}"
        con = db()
        cur = con.cursor()
        cur.execute("""
        INSERT INTO tasks(user_id, task_text, task_id, msg_id, status, created_at)
        VALUES(?,?,?,?,?,?)
        """, (int(user_id), text, task_id, int(msg_id), "fetched", int(time.time())))
        con.commit()
        con.close()

# ========= CONFIRM =========
async def confirm_task(user_id):
    idx, client = get_client()
    if client is None:
        print("[USERBOT] ⚠️ No client available for confirm_task")
        return

    con = db()
    cur = con.cursor()
    cur.execute("""
    SELECT task_id, msg_id FROM tasks
    WHERE user_id=?
    ORDER BY id DESC LIMIT 1
    """, (int(user_id),))
    row = cur.fetchone()
    con.close()
    if not row:
        return
    task_id, msg_id = row["task_id"], row["msg_id"]

    async with locks[idx]:
        msg = await client.get_messages(SOURCE, ids=int(msg_id))
        await click_button(msg, ["done", "✓"])
        for _ in range(30):
            await asyncio.sleep(1)
            updated = await client.get_messages(SOURCE, ids=int(msg_id))
            text = (updated.text or "").lower()
            if "how to logout" in text or "done" in text:
                save_result(user_id, task_id, "success")
                return
            if "try again" in text or "not done" in text:
                save_result(user_id, task_id, "fail")
                return

def save_result(user_id, task_id, status):
    con = db()
    cur = con.cursor()
    cur.execute("""
    INSERT INTO results(user_id, task_id, status, created_at)
    VALUES(?,?,?,?)
    """, (int(user_id), str(task_id), str(status), int(time.time())))
    con.commit()
    con.close()

# ========= JOB QUEUE WORKER =========
def _claim_next_job_sync():
    con = db()
    cur = con.cursor()
    cur.execute("""
        SELECT * FROM jobs
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 1
    """)
    job = cur.fetchone()
    if not job:
        con.close()
        return None

    cur.execute(
        "UPDATE jobs SET status='processing', updated_at=? WHERE id=? AND status='pending'",
        (int(time.time()), int(job["id"])),
    )
    if cur.rowcount != 1:
        con.commit()
        con.close()
        return None

    con.commit()
    con.close()
    return dict(job)

def _finish_job_sync(job_id: int, status: str, error: str = ""):
    con = db()
    cur = con.cursor()
    cur.execute(
        "UPDATE jobs SET status=?, updated_at=?, error=? WHERE id=?",
        (str(status), int(time.time()), str(error or ""), int(job_id)),
    )
    con.commit()
    con.close()

async def _job_loop():
    while True:
        try:
            job = await asyncio.to_thread(_claim_next_job_sync)
            if not job:
                await asyncio.sleep(1)
                continue

            try:
                if job["job_type"] == "fetch":
                    await fetch_task(job["user_id"])
                elif job["job_type"] == "confirm":
                    await confirm_task(job["user_id"])
                else:
                    print(f"[USERBOT] ⚠️ Unknown job_type: {job['job_type']}")
                await asyncio.to_thread(_finish_job_sync, int(job["id"]), "done", "")
            except Exception as e:
                print("[USERBOT] ❌ job failed:", repr(e))
                await asyncio.to_thread(_finish_job_sync, int(job["id"]), "failed", repr(e))

        except Exception as e:
            print("[USERBOT] ❌ job loop error:", repr(e))
            await asyncio.sleep(2)

async def handle_job(job):
    """Compatibility helper for same-process usage."""
    if not isinstance(job, dict):
        return
    job_type = job.get("type")
    user_id = job.get("user")
    if job_type == "fetch":
        await fetch_task(user_id)
    elif job_type == "confirm":
        await confirm_task(user_id)

# ========= START USERBOT =========
async def start_userbot():
    global _started, _job_task

    if _started:
        print("[USERBOT] ⚠️ Already started")
        return
    _started = True

    init_db()

    if not clients:
        print("[USERBOT] ⚠️ No clients loaded. Userbot will stay idle.")

    for i, c in enumerate(clients):
        try:
            print(f"[USERBOT] Connecting client {i}...")
            await c.connect()
            if not await c.is_user_authorized():
                print(f"[USERBOT] ⚠️ Client {i} not authorized, skipping")
                continue
            print(f"[USERBOT] ✅ CLIENT READY: {i}")
        except Exception as e:
            print(f"[USERBOT] ❌ Client {i} ERROR: {repr(e)}")

    _job_task = asyncio.create_task(_job_loop())
    print("[USERBOT] ✅ Worker loop started")

    await asyncio.Event().wait()

async def _keep_alive():
    while True:
        await asyncio.sleep(5)

if __name__ == "__main__":
    print("USERBOT started directly...")
    asyncio.run(start_userbot())
    
