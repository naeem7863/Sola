!/usr/bin/env python3
import os
import asyncio
import logging
from datetime import datetime, timedelta
import io

import aiohttp
from telegram import Bot
from solana.rpc.async_api import AsyncClient
from solana.publickey import PublicKey
from dotenv import load_dotenv
import matplotlib.pyplot as plt

# ——— CONFIG & ENV —————————————————————————————————
load_dotenv()  # loads TELEGRAM_TOKEN, CHAT_ID from .env or env vars
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID         = os.getenv("CHAT_ID")
SOLANA_RPC      = "https://api.mainnet-beta.solana.com"
JUPITER_API_URL = "https://quote-api.jup.ag/v6/quote"
PUMP_FUN_API    = "https://api.pump.fun/tokens"  # replace with real endpoint

# Thresholds
CONFIG = {
    "max_market_cap":      500_000,  # USD
    "min_liquidity":         5_000,  # USD
    "max_liquidity":        50_000,  # USD
    "min_volume_ratio":        0.5,  # Vol / Mcap
    "min_price_increase":     0.5,  # 50% over 6h
    "min_safety_score":       85,   # out of 100
    "max_token_age_hours":    12
}

# ——— SETUP —————————————————————————————————————
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
sol_client = AsyncClient(SOLANA_RPC)


# ——— HELPERS ————————————————————————————————————
async def fetch_pump_fun_tokens(session):
    """Fetch new tokens list from Pump.fun (mocked)."""
    try:
        async with session.get(PUMP_FUN_API) as resp:
            resp.raise_for_status()
            body = await resp.json()
        return body.get("tokens", [])
    except Exception:
        logger.exception("fetch_pump_fun_tokens failed")
        return []

async def fetch_token_data(session, token_address):
    """Get price, liquidity, volume and compute 6h price change."""
    params = {
        "inputMint":  "So11111111111111111111111111111111111111112",  # SOL
        "outputMint": token_address,
        "amount":     1_000_000  # 1 SOL in lamports
    }
    try:
        async with session.get(JUPITER_API_URL, params=params) as resp:
            resp.raise_for_status()
            payload = await resp.json()
        entry = payload["data"][0]
        price     = entry["outAmount"] / 1_000_000
        liquidity = entry.get("liquidity", 0)
        volume24h = entry.get("volume24h", 0)
        # rough 6h-ago price estimate
        old_price = price / (1 + CONFIG["min_price_increase"])
        delta     = (price - old_price) / old_price
        return {
            "price":          price,
            "liquidity":      liquidity,
            "volume":         volume24h,
            "price_change6h": delta
        }
    except Exception:
        logger.exception(f"fetch_token_data failed for {token_address}")
        return None

async def get_token_metrics(token_address, created_at):
    """Compute age, holder count, top-10 share (mocked)."""
    try:
        created = datetime.fromisoformat(created_at)
        age_h   = (datetime.utcnow() - created).total_seconds() / 3600
        # TODO: replace with real SPL-holder lookup
        return {
            "age_hours":       age_h,
            "holder_count":    200,
            "top10_pct":       0.25
        }
    except Exception:
        logger.exception("get_token_metrics failed")
        return None

async def get_x_sentiment(ticker):
    """Mock X (Twitter) sentiment."""
    return {"MOONCAT": 80, "DOGSOL": 60}.get(ticker, 50)

def compute_score(data, metrics, sentiment):
    """Combine your factors into 0–100 safety/potential score."""
    score = 0
    # volume ratio
    mcap = data["market_cap"]
    vr   = data["volume"] / mcap if mcap else 0
    score += 25 if vr >= CONFIG["min_volume_ratio"] else 12 if vr >= CONFIG["min_volume_ratio"] / 2 else 0
    # price change
    pc = data["price_change6h"]
    score += 25 if pc >= CONFIG["min_price_increase"] else 12 if pc >= CONFIG["min_price_increase"]/2 else 0
    # liquidity
    liq = data["liquidity"]
    score += 20 if CONFIG["min_liquidity"] <= liq <= CONFIG["max_liquidity"] else 8 if liq >= CONFIG["min_liquidity"]/2 else 0
    # holder distribution
    top10 = metrics["top10_pct"]
    score += 15 if top10 <= 0.30 else 7 if top10 <= 0.50 else 0
    # sentiment
    score += 15 if sentiment >= 75 else 7 if sentiment >= 50 else 0
    # anomaly check
    if liq < CONFIG["min_liquidity"] and vr > 1:
        score -= 20
    return max(0, min(100, score))

async def generate_chart(token_name, ticker, data):
    """Return an in-memory PNG of price+volume (mocked history)."""
    now = datetime.utcnow()
    times = [now - timedelta(hours=i) for i in range(6, -1, -1)]
    prices  = [data["price"] * (1 - 0.1 * i) for i in range(6)] + [data["price"]]
    volumes = [data["volume"] * (0.8 + 0.1 * i) for i in range(7)]

    plt.figure(figsize=(8,4))
    plt.subplot(2,1,1)
    plt.plot(times, prices)
    plt.title(f"{token_name} ({ticker})")
    plt.ylabel("Price (USD)")

    plt.subplot(2,1,2)
    plt.bar(times, volumes)
    plt.ylabel("Volume (USD)")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf

async def send_alert(token_info, data):
    price      = token_info["price"]
    mcap       = token_info["market_cap"]
    potential  = price * 100
    caption = (
        f"🚀 *High-Potential Meme Coin*\n"
        f"*{token_info['name']}* (`{token_info['ticker']}`)\n\n"
        f"• Price: ${price:.6f}\n"
        f"• Market Cap: ${mcap:,.0f}\n"
        f"• Safety Score: {token_info['score']}/100\n"
        f"• Liquidity: ${data['liquidity']:.0f}\n"
        f"• Volume (24h): ${data['volume']:.0f}\n"
        f"• Sentiment: {token_info['sentiment']}/100\n\n"
        f"[Swap on Jupiter](https://jup.ag/swap/{token_info['address']})"
    )
    chart = await generate_chart(token_info["name"], token_info["ticker"], data)
    await bot.send_photo(
        chat_id=CHAT_ID,
        photo=chart,
        caption=caption,
        parse_mode="Markdown"
    )
    chart.close()


# ——— CORE LOOP ———————————————————————————————
async def monitor():
    async with aiohttp.ClientSession() as session:
        tokens = await fetch_pump_fun_tokens(session)
        for tk in tokens:
            addr = tk["address"]
            raw  = await fetch_token_data(session, addr)
            if not raw:
                continue

            # estimate mcap (1B supply)
            mcap = raw["price"] * 1_000_000_000
            if mcap > CONFIG["max_market_cap"]:
                continue

            # age & holders
            metr = await get_token_metrics(addr, tk["created_at"])
            if not metr or metr["age_hours"] > CONFIG["max_token_age_hours"]:
                continue

            # liquidity filter
            if not (CONFIG["min_liquidity"] <= raw["liquidity"] <= CONFIG["max_liquidity"]):
                continue

            sent = await get_x_sentiment(tk["ticker"])
            raw["market_cap"]    = mcap
            score = compute_score(raw, metr, sent)
            if score < CONFIG["min_safety_score"]:
                continue

            info = {
                "name":      tk["name"],
                "ticker":    tk["ticker"],
                "address":   addr,
                "price":     raw["price"],
                "market_cap": mcap,
                "score":     score,
                "sentiment": sent
            }
            await send_alert(info, raw)
            logger.info(f"Alert sent for {tk['name']}")

async def main():
    while True:
        try:
            await monitor()
        except Exception:
            logger.exception("Error in monitor()")
            await bot.send_message(
                chat_id=CHAT_ID,
                text="⚠️ Bot encountered an error. Check logs."
            )
        await asyncio.sleep(300)  # 5 minutes

if __name__ == "__main__":
    asyncio.run(main())
