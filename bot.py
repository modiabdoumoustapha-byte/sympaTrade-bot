import os
import logging
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8867216030:AAHBSX1AVQ07zMb_h8DVPvqDbhtxhFe-zVE")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "7040679059")
TD_KEY  = os.environ.get("TWELVE_DATA_KEY", "e5e556c8147a41bd9b001d3c853e1f8c")

PAIRS = [
    "EUR/USD","GBP/USD","USD/JPY","USD/CHF","AUD/USD",
    "NZD/USD","USD/CAD","EUR/GBP","EUR/JPY","GBP/JPY",
    "EUR/AUD","GBP/AUD","AUD/JPY","EUR/CAD","GBP/CAD",
    "USD/SGD","EUR/NZD","GBP/NZD","CAD/JPY","CHF/JPY",
    "XAU/USD"
]

STATE = {
    "pending": None,
    "active": True,
    "mode": "demo",
    "risk": 1.0,
    "positions": [],
    "trades": 0,
}

def niamey_hour():
    return datetime.now(timezone(timedelta(hours=1))).hour

def is_session():
    h = niamey_hour()
    dow = datetime.now(timezone(timedelta(hours=1))).weekday()
    if dow in [5, 6]: return False
    if dow == 4 and h >= 20: return False
    return (8 <= h < 12) or (14 <= h < 22)

def get_session_name():
    h = niamey_hour()
    if 8 <= h < 12: return "🇬🇧 Londres"
    if 14 <= h < 22: return "🇺🇸 New York"
    return "🌙 Hors session"

def calc_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period + 2: return 50
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    for i in range(period, len(prices)-1):
        d = prices[i+1] - prices[i]
        ag = (ag * (period-1) + max(d, 0)) / period
        al = (al * (period-1) + max(-d, 0)) / period
    return 100 if al == 0 else 100 - (100 / (1 + ag/al))

async def fetch_data(symbol):
    sym = symbol.replace("/", "")
    url = f"https://api.twelvedata.com/time_series?symbol={sym}&interval=5min&outputsize=55&apikey={TD_KEY}&format=JSON"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                d = await r.json()
                if d.get("status") == "ok":
                    return list(reversed(d["values"]))
    except Exception as e:
        logger.error(f"fetch {symbol}: {e}")
    return None

async def analyse(symbol):
    candles = await fetch_data(symbol)
    if not candles or len(candles) < 50: return None

    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    opens  = [float(c["open"])  for c in candles]

    score = 0
    direction = 0
    signals = []

    # EMA
    e9  = calc_ema(closes, 9)
    e21 = calc_ema(closes, 21)
    e50 = calc_ema(closes, 50)
    if e9 and e21 and e50:
        if e9[-1] > e21[-1] > e50[-1]:
            score += 1; direction = 1
            signals.append("📈 EMA alignées haussières")
        elif e9[-1] < e21[-1] < e50[-1]:
            score += 1; direction = -1
            signals.append("📉 EMA alignées baissières")

    # RSI
    rsi = calc_rsi(closes)
    if rsi > 50 and direction >= 0:
        score += 1; direction = 1
        signals.append(f"⚡ RSI {rsi:.0f} — zone haussière")
    elif rsi < 50 and direction <= 0:
        score += 1; direction = -1
        signals.append(f"⚡ RSI {rsi:.0f} — zone baissière")

    # MACD
    fast = calc_ema(closes, 12)
    slow = calc_ema(closes, 26)
    if fast and slow:
        ml = fast[-1] - slow[-1]
        ml2 = fast[-2] - slow[-2] if len(fast) > 1 and len(slow) > 1 else ml
        if ml > 0 and ml > ml2 and direction >= 0:
            score += 1; direction = 1
            signals.append("🔄 MACD momentum haussier")
        elif ml < 0 and ml < ml2 and direction <= 0:
            score += 1; direction = -1
            signals.append("🔄 MACD momentum baissier")

    # ICT/SMC simple
    price = closes[-1]
    swH = max(highs[-20:-1])
    swL = min(lows[-20:-1])
    bos_bull = highs[-1] > swH
    bos_bear = lows[-1] < swL
    ob_bull = any(closes[i] < opens[i] and lows[i] <= price <= highs[i] for i in range(-10, -1))
    ob_bear = any(closes[i] > opens[i] and lows[i] <= price <= highs[i] for i in range(-10, -1))

    if (bos_bull or ob_bull) and direction >= 0:
        score += 1; direction = 1
        signals.append("🧠 ICT/SMC — structure haussière")
    elif (bos_bear or ob_bear) and direction <= 0:
        score += 1; direction = -1
        signals.append("🧠 ICT/SMC — structure baissière")

    if score < 3 or direction == 0: return None

    pip = 0.1 if "XAU" in symbol else (0.01 if "JPY" in symbol else 0.0001)
    entry = price
    sl = round(entry - 15*pip, 5) if direction == 1 else round(entry + 15*pip, 5)
    tp = round(entry + 30*pip, 5) if direction == 1 else round(entry - 30*pip, 5)

    return {
        "symbol": symbol, "direction": direction,
        "entry": entry, "sl": sl, "tp": tp,
        "score": score, "signals": signals,
        "rsi": round(rsi, 1), "session": get_session_name(),
        "time": datetime.now(timezone(timedelta(hours=1))).strftime("%H:%M")
    }

async def send_signal(app, sig):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    d = "ACHAT ▲ BUY" if sig["direction"] == 1 else "VENTE ▼ SELL"
    mode = "🔵 DÉMO" if STATE["mode"] == "demo" else "🔴 RÉEL"
    conf = "\n".join([f"  • {s}" for s in sig["signals"]])
    msg = (
        f"{emoji} *SIGNAL {d}*  {mode}\n"
        f"{'─'*28}\n"
        f"📊 *Paire :* `{sig['symbol']}`\n"
        f"💵 *Entrée :* `{sig['entry']}`\n"
        f"🛑 *Stop Loss :* `{sig['sl']}` — 15 pips\n"
        f"🎯 *Take Profit :* `{sig['tp']}` — 30 pips\n"
        f"📦 *R/R :* 1:2\n"
        f"📈 *Score :* {sig['score']}/4\n"
        f"📉 *RSI :* {sig['rsi']}\n"
        f"⏰ *Session :* {sig['session']}\n"
        f"🕐 *Heure :* {sig['time']}\n"
        f"{'─'*28}\n"
        f"*Confluences :*\n{conf}\n"
        f"{'─'*28}\n"
        f"⚡ *Que veux-tu faire ?*"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ VALIDER", callback_data="CONFIRM"),
        InlineKeyboardButton("❌ ANNULER", callback_data="CANCEL"),
    ],[
        InlineKeyboardButton("⏭ SUIVANT",  callback_data="SKIP"),
    ]])
    await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", reply_markup=kb)

async def scan_loop(app):
    await asyncio.sleep(5)
    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🚀 *SympaTrade Bot démarré !*\n\n"
            f"📡 {len(PAIRS)} paires surveillées\n"
            "🧠 ICT/SMC + EMA + RSI + MACD\n"
            "🔵 Mode DÉMO actif\n\n"
            "Tape /help pour les commandes."
        ),
        parse_mode="Markdown"
    )
    while True:
        try:
            if STATE["active"] and not STATE["pending"] and is_session():
                for symbol in PAIRS:
                    if STATE["pending"]: break
                    sig = await analyse(symbol)
                    if sig:
                        STATE["pending"] = sig
                        await send_signal(app, sig)
                        break
                    await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Scan error: {e}")
        await asyncio.sleep(60)

async def btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    sig = STATE["pending"]
    if q.data == "CONFIRM" and sig:
        STATE["pending"] = None
        STATE["trades"] += 1
        STATE["positions"].append(sig)
        d = "ACHAT ✅" if sig["direction"] == 1 else "VENTE ✅"
        await q.edit_message_text(
            f"✅ *Trade EXÉCUTÉ !*\n\n{d} `{sig['symbol']}`\n"
            f"💵 `{sig['entry']}` | 🛑 `{sig['sl']}` | 🎯 `{sig['tp']}`\n"
            f"🆔 Trade #{STATE['trades']}",
            parse_mode="Markdown"
        )
    elif q.data in ["CANCEL", "SKIP"] and sig:
        STATE["pending"] = None
        txt = "❌ Trade annulé." if q.data == "CANCEL" else "⏭ Signal ignoré."
        await q.edit_message_text(txt + " Scan reprend...", parse_mode="Markdown")

async def cmd_start(u: Update, c):
    await u.message.reply_text(
        "🤖 *SympaTrade Bot v3.0*\n\n"
        "/status — État du bot\n/positions — Positions ouvertes\n"
        "/scan — Scan manuel\n/close — Fermer tout\n"
        "/demo — Mode démo\n/real — Mode réel\n/help — Aide",
        parse_mode="Markdown"
    )

async def cmd_status(u: Update, c):
    h = niamey_hour()
    now = datetime.now(timezone(timedelta(hours=1)))
    await u.message.reply_text(
        f"📊 *STATUT BOT*\n"
        f"🤖 {'Actif ✅' if STATE['active'] else 'En pause ⏸'}\n"
        f"💼 {'🔵 DÉMO' if STATE['mode']=='demo' else '🔴 RÉEL'}\n"
        f"📋 Positions : {len(STATE['positions'])}\n"
        f"⚡ Kill Zone : {'✅' if is_session() else '❌'}\n"
        f"⏰ Niamey : {now.strftime('%H:%M')}",
        parse_mode="Markdown"
    )

async def cmd_scan(u: Update, c):
    await u.message.reply_text("🔍 Scan en cours...", parse_mode="Markdown")
    if not STATE["pending"]:
        for symbol in PAIRS:
            sig = await analyse(symbol)
            if sig:
                STATE["pending"] = sig
                await send_signal(c.application, sig)
                return
            await asyncio.sleep(2)
        await u.message.reply_text("Aucun signal trouvé pour le moment.", parse_mode="Markdown")

async def cmd_close(u: Update, c):
    n = len(STATE["positions"])
    STATE["positions"] = []
    STATE["pending"] = None
    await u.message.reply_text(f"🔒 {n} position(s) fermée(s).", parse_mode="Markdown")

async def cmd_demo(u: Update, c):
    STATE["mode"] = "demo"
    await u.message.reply_text("🔵 Mode DÉMO activé.", parse_mode="Markdown")

async def cmd_real(u: Update, c):
    STATE["mode"] = "real"
    await u.message.reply_text("🔴 Mode RÉEL activé. ⚠️ Trades réels !", parse_mode="Markdown")

async def cmd_help(u: Update, c):
    await u.message.reply_text(
        "📖 *AIDE SYMPATRADE*\n\n"
        "Le bot scanne 21 paires toutes les 60s\n"
        "Signal envoyé si 3+ confluences\n\n"
        "🇬🇧 Londres : 8h-12h\n🇺🇸 New York : 14h-22h\n\n"
        "/status /scan /positions /close\n/demo /real /help",
        parse_mode="Markdown"
    )

async def cmd_positions(u: Update, c):
    if not STATE["positions"]:
        await u.message.reply_text("📋 Aucune position ouverte.", parse_mode="Markdown")
        return
    msg = "📋 *POSITIONS*\n\n"
    for i, p in enumerate(STATE["positions"], 1):
        d = "▲" if p["direction"] == 1 else "▼"
        msg += f"{i}. {d} `{p['symbol']}` @ `{p['entry']}`\n"
    await u.message.reply_text(msg, parse_mode="Markdown")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("close",     cmd_close))
    app.add_handler(CommandHandler("demo",      cmd_demo))
    app.add_handler(CommandHandler("real",      cmd_real))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CallbackQueryHandler(btn))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await scan_loop(app)

    loop.run_until_complete(run())

if __name__ == "__main__":
    main()
    
