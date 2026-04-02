import asyncio
import subprocess
import sys

async def start_process(name, cmd):
    while True:
        print(f"🚀 Starting {name}...")

        process = await asyncio.create_subprocess_exec(
            sys.executable, cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        async def log_stream(stream, prefix):
            while True:
                line = await stream.readline()
                if not line:
                    break
                print(f"[{prefix}] {line.decode().strip()}")

        await asyncio.gather(
            log_stream(process.stdout, name),
            log_stream(process.stderr, name),
        )

        print(f"❌ {name} crashed. Restarting in 3 sec...")
        await asyncio.sleep(3)

async def main():
    await asyncio.gather(
        start_process("MAIN BOT", "bot_app.py"),
        start_process("USERBOT", "userbot.py"),
    )

if __name__ == "__main__":
    asyncio.run(main())
