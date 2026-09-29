from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright


async def main() -> None:
    load_dotenv()
    profile = Path(os.getenv("PUMPFUN_CALLOUT_PROFILE_DIR", "data/pumpfun-profile"))
    profile.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://pump.fun", wait_until="domcontentloaded")
        print("Log into Pump.fun in the opened browser window.")
        print("When you are signed in, press ENTER here to save the session and exit.")
        await asyncio.to_thread(input)
        await context.close()
        print(f"Saved Pump.fun browser profile at: {profile}")


if __name__ == "__main__":
    asyncio.run(main())
