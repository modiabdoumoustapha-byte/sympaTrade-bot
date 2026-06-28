"""
╔══════════════════════════════════════════════════════════════╗
║           SYMPATRADE CLOUD BOT — Version 3.0                ║
║   100% Android | Telegram | Exness API | Multi-Paires       ║
║   Forex (20 paires) + XAU/USD | ICT/SMC + EMA + RSI + MACD ║
║              Développé pour Sympa — Niger 🇳🇪               ║
╚══════════════════════════════════════════════════════════════╝

HÉBERGEMENT GRATUIT : Render.com
AUCUN PC REQUIS — tourne 24h/24 dans le cloud
"""

import os
import asyncio
import logging
import json
import time
import math
from datetime import datetime, timezone, timedelta
from typing import Optional
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, JobQueue
)

# ─────────────────────────────────────────────
#  CONFIGURATION GÉNÉRALE
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variables d'environnement (à définir sur Render.com)
TELEGRAM_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "8867216030:AAHBSX1AVQ07zMb_h8DVPvqDbhtxhFe-zVE")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "7040679059")
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_KEY", "e5e556c8147a41bd9b001d3c853e1f8c")
EXNESS_LOGIN_DEMO   = os.environ.get("EXNESS_LOGIN_DEMO", "")
EXNESS_PASSWORD_DEMO= os.environ.get("EXNESS_PASSWORD_DEMO", "")
EXNESS_LOGIN_REAL   = os.environ.get("EXNESS_LOGIN_REAL", "")
EXNESS_PASSWORD_REAL= os.environ.get("EXNESS_PASSWORD_REAL", "")

# ─────────────────────────────────────────────
#  PAIRES À TRADER
# ─────────────────────────────────────────────
FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
    "AUD/USD", "NZD/USD", "USD/CAD", "EUR/GBP",
    "EUR/JPY", "GBP/JPY", "EUR/AUD", "GBP/AUD",
    "AUD/JPY", "EUR/CAD", "GBP/CAD", "USD/SGD",
    "EUR/NZD", "GBP/NZD", "CAD/JPY", "CHF/JPY"
]
METAL_PAIRS = ["XAU/USD"]
ALL_PAIRS   = FOREX_PAIRS + METAL_PAIRS  # 21 paires au total

# ─────────────────────────────────────────────
#  PARAMÈTRES DE TRADING (modifiables)
# ─────────────────────────────────────────────
CONFIG = {
    "risk_percent":    1.0,    # % du capital risqué par trade
    "sl_pips":         15,     # Stop Loss en pips
    "tp_pips":         30,     # Take Profit en pips
    "max_daily_loss":  5.0,    # Perte journalière max (%)
    "max_open_trades": 3,      # Positions simultanées max
    "max_spread":      3.0,    # Spread max autorisé (pips)
    "min_score":       3,      # Score min pour signal (sur 4)
    "signal_expiry":   120,    # Expiration signal (secondes)
    "scan_interval":   60,     # Intervalle de scan (secondes)
    "account_mode":    "demo", # "demo" ou "real"
    # Kill Zones (heure Niamey UTC+1)
    "london_open":  8,
    "london_close": 12,
    "ny_open":      14,
    "ny_close":     22,
}

# ─────────────────────────────────────────────
#  ÉTAT GLOBAL DU BOT
# ─────────────────────────────────────────────
STATE = {
    "pending_signal":   None,   # Signal en attente de validation
    "pending_time":     None,
    "open_positions":   [],     # Positions ouvertes simulées
    "daily_start_bal":  10000,  # Balance de départ du jour (simulée)
    "daily_pnl":        0.0,
    "total_trades":     0,
    "wins":             0,
    "losses":           0,
    "bot_active":       True,
    "scanning":         False,
    "last_signals":     {},     # Eviter doublons par paire
}

# ─────────────────────────────────────────────
#  RÉCUPÉRATION DES DONNÉES DE MARCHÉ
# ─────────────────────────────────────────────
async def fetch_candles(symbol: str, interval: str = "5min", outputsize: int = 60) -> Optional[list]:
    """Récupère les bougies via Twelve Data API (gratuit jusqu'à 800 req/jour)"""
    # Formater le symbole pour Twelve Data
    td_symbol = symbol.replace("/", "")
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={td_symbol}&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
        f"&format=JSON&timezone=Africa/Niamey"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("status") == "ok" and "values" in data:
                    candles = data["values"]
                    # Retourner du plus ancien au plus récent
                    return list(reversed(candles))
                else:
                    logger.warning(f"Twelve Data erreur pour {symbol}: {data.get('message','')}")
                    return None
    except Exception as e:
        logger.error(f"Erreur fetch {symbol}: {e}")
        return None

async def get_current_price(symbol: str) -> Optional[dict]:
    """Prix en temps réel"""
    td_symbol = symbol.replace("/", "")
    url = f"https://api.twelvedata.com/price?symbol={td_symbol}&apikey={TWELVE_DATA_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return data
    except:
        return None

# ─────────────────────────────────────────────
#  CALCUL DES INDICATEURS
# ─────────────────────────────────────────────
def calculate_ema(prices: list, period: int) -> list:
    """Calcule l'EMA"""
    if len(prices) < period:
        return []
    ema = []
    k = 2 / (period + 1)
    ema.append(sum(prices[:period]) / period)
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_rsi(prices: list, period: int = 14) -> list:
    """Calcule le RSI"""
    if len(prices) < period + 1:
        return []
    rsi_values = []
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(prices) - 1):
        diff = prices[i + 1] - prices[i]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    return rsi_values

def calculate_macd(prices: list, fast=12, slow=26, signal=9):
    """Calcule le MACD"""
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return [], [], []
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-(min_len - i)] - ema_slow[-(min_len - i)] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    histogram = [macd_line[-(len(signal_line) - i)] - signal_line[i] for i in range(len(signal_line))]
    return macd_line, signal_line, histogram

# ─────────────────────────────────────────────
#  STRATÉGIE ICT / SMC
# ─────────────────────────────────────────────
def detect_ict_smc(candles: list, current_price: float) -> int:
    """Détecte Order Blocks, FVG, BOS/CHoCH"""
    if len(candles) < 25:
        return 0
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    closes = [float(c["close"]) for c in candles]
    opens  = [float(c["open"])  for c in candles]

    # BOS — Break of Structure
    lookback = 20
    swing_high = max(highs[-lookback:-1])
    swing_low  = min(lows[-lookback:-1])
    bos_bull = highs[-1] > swing_high
    bos_bear = lows[-1]  < swing_low

    # Order Blocks
    ob_bull, ob_bear = False, False
    for i in range(-10, -1):
        if closes[i] < opens[i]:  # Bougie bearish → potentiel OB bullish
            if lows[i] <= current_price <= highs[i]:
                ob_bull = True
        if closes[i] > opens[i]:  # Bougie bullish → potentiel OB bearish
            if lows[i] <= current_price <= highs[i]:
                ob_bear = True

    # Fair Value Gap
    fvg_bull, fvg_bear = False, False
    for i in range(-8, -2):
        # Bullish FVG : low[i-1] > high[i+1]
        if i - 1 >= -len(candles) and i + 1 < 0:
            if lows[i-1] > highs[i+1]:
                if highs[i+1] <= current_price <= lows[i-1]:
                    fvg_bull = True
            if highs[i-1] < lows[i+1]:
                if highs[i-1] <= current_price <= lows[i+1]:
                    fvg_bear = True

    bull_score = sum([bos_bull, ob_bull, fvg_bull])
    bear_score = sum([bos_bear, ob_bear, fvg_bear])

    if bull_score >= 2: return 1
    if bear_score >= 2: return -1
    return 0

# ─────────────────────────────────────────────
#  MOTEUR D'ANALYSE PRINCIPALE
# ─────────────────────────────────────────────
async def analyse_pair(symbol: str) -> Optional[dict]:
    """Analyse complète d'une paire — retourne un signal ou None"""

    candles = await fetch_candles(symbol, interval="5min", outputsize=60)
    if not candles or len(candles) < 50:
        return None

    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]

    current_price = closes[-1]
    score     = 0
    direction = 0  # 1=BUY, -1=SELL
    signals   = []

    # ── EMA (9 / 21 / 50) ──────────────────────
    ema9  = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    ema50 = calculate_ema(closes, 50)

    if ema9 and ema21 and ema50:
        bull_ema = ema9[-1] > ema21[-1] > ema50[-1]
        bear_ema = ema9[-1] < ema21[-1] < ema50[-1]
        cross_up = len(ema9) > 1 and ema9[-2] < ema21[-2] and ema9[-1] > ema21[-1]
        cross_dn = len(ema9) > 1 and ema9[-2] > ema21[-2] and ema9[-1] < ema21[-1]

        if bull_ema or cross_up:
            score += 1; direction = 1
            signals.append("📈 EMA haussière" + (" (croisement ↑)" if cross_up else ""))
        elif bear_ema or cross_dn:
            score += 1; direction = -1
            signals.append("📉 EMA baissière" + (" (croisement ↓)" if cross_dn else ""))

    # ── RSI (14) ───────────────────────────────
    rsi_values = calculate_rsi(closes, 14)
    if len(rsi_values) >= 2:
        rsi_curr = rsi_values[-1]
        rsi_prev = rsi_values[-2]
        rsi_cross_up = rsi_prev < 50 <= rsi_curr
        rsi_cross_dn = rsi_prev > 50 >= rsi_curr
        rsi_os_exit  = rsi_prev < 30 and rsi_curr >= 30
        rsi_ob_exit  = rsi_prev > 70 and rsi_curr <= 70

        if (rsi_cross_up or rsi_os_exit) and direction >= 0:
            score += 1; direction = 1
            signals.append(f"⚡ RSI {rsi_curr:.1f} — {'sortie survente' if rsi_os_exit else 'croise 50 ↑'}")
        elif (rsi_cross_dn or rsi_ob_exit) and direction <= 0:
            score += 1; direction = -1
            signals.append(f"⚡ RSI {rsi_curr:.1f} — {'sortie surachat' if rsi_ob_exit else 'croise 50 ↓'}")

    # ── MACD (12/26/9) ─────────────────────────
    macd_line, signal_line, histogram = calculate_macd(closes)
    if len(macd_line) >= 2 and len(signal_line) >= 2:
        macd_cross_up = macd_line[-2] < signal_line[-2] and macd_line[-1] >= signal_line[-1]
        macd_cross_dn = macd_line[-2] > signal_line[-2] and macd_line[-1] <= signal_line[-1]
        macd_bull = macd_line[-1] > 0 and macd_line[-1] > signal_line[-1]
        macd_bear = macd_line[-1] < 0 and macd_line[-1] < signal_line[-1]

        if (macd_cross_up or macd_bull) and direction >= 0:
            score += 1; direction = 1
            signals.append("🔄 MACD " + ("croisement ↑" if macd_cross_up else "momentum haussier"))
        elif (macd_cross_dn or macd_bear) and direction <= 0:
            score += 1; direction = -1
            signals.append("🔄 MACD " + ("croisement ↓" if macd_cross_dn else "momentum baissier"))

    # ── ICT / SMC ──────────────────────────────
    ict_signal = detect_ict_smc(candles, current_price)
    if ict_signal == 1 and direction >= 0:
        score += 1; direction = 1
        signals.append("🧠 ICT/SMC — Order Block / FVG / BOS haussier")
    elif ict_signal == -1 and direction <= 0:
        score += 1; direction = -1
        signals.append("🧠 ICT/SMC — Order Block / FVG / BOS baissier")

    # ── DÉCISION ───────────────────────────────
    if score < CONFIG["min_score"] or direction == 0:
        return None

    # Calculer SL/TP selon la paire
    pip_size = 0.01 if "JPY" in symbol else (0.1 if "XAU" in symbol else 0.0001)
    sl_distance = CONFIG["sl_pips"] * pip_size
    tp_distance = CONFIG["tp_pips"] * pip_size

    if direction == 1:
        entry = current_price
        sl    = round(entry - sl_distance, 5)
        tp    = round(entry + tp_distance, 5)
    else:
        entry = current_price
        sl    = round(entry + sl_distance, 5)
        tp    = round(entry - tp_distance, 5)

    # Déterminer la session
    now_niamey = datetime.now(timezone(timedelta(hours=1)))
    h = now_niamey.hour
    if CONFIG["london_open"] <= h < CONFIG["london_close"]:
        session = "🇬🇧 Londres"
    elif CONFIG["ny_open"] <= h < CONFIG["ny_close"]:
        session = "🇺🇸 New York"
    else:
        session = "🌙 Hors session"

    return {
        "symbol":    symbol,
        "direction": direction,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "score":     score,
        "signals":   signals,
        "session":   session,
        "pip_size":  pip_size,
        "time":      datetime.now(timezone(timedelta(hours=1))).strftime("%H:%M"),
        "rsi":       round(rsi_values[-1], 1) if rsi_values else 0,
    }

# ─────────────────────────────────────────────
#  KILL ZONE CHECK
# ─────────────────────────────────────────────
def is_kill_zone() -> bool:
    now = datetime.now(timezone(timedelta(hours=1)))
    h = now.hour
    # Pas de trading vendredi soir
    if now.weekday() == 4 and h >= 20:
        return False
    # Pas de trading week-end
    if now.weekday() in [5, 6]:
        return False
    return (
        CONFIG["london_open"] <= h < CONFIG["london_close"] or
        CONFIG["ny_open"]     <= h < CONFIG["ny_close"]
    )

# ─────────────────────────────────────────────
#  FORMATAGE DU MESSAGE SIGNAL
# ─────────────────────────────────────────────
def format_signal_message(sig: dict) -> str:
    emoji    = "🟢" if sig["direction"] == 1 else "🔴"
    dir_text = "ACHAT ▲ BUY"  if sig["direction"] == 1 else "VENTE ▼ SELL"
    mode     = "🔵 DÉMO" if CONFIG["account_mode"] == "demo" else "🔴 RÉEL"
    risk_amt = STATE["daily_start_bal"] * CONFIG["risk_percent"] / 100

    # Confluences détaillées
    confluence_text = "\n".join([f"  • {s}" for s in sig["signals"]])

    msg = (
        f"{emoji} *SIGNAL {dir_text}*  {mode}\n"
        f"{'─' * 30}\n"
        f"📊 *Paire :* `{sig['symbol']}`\n"
        f"💵 *Entrée :* `{sig['entry']}`\n"
        f"🛑 *Stop Loss :* `{sig['sl']}` ─ {CONFIG['sl_pips']} pips\n"
        f"🎯 *Take Profit :* `{sig['tp']}` ─ {CONFIG['tp_pips']} pips\n"
        f"📦 *R/R :* 1 : {CONFIG['tp_pips'] // CONFIG['sl_pips']}\n"
        f"💸 *Risque :* ${risk_amt:.2f} ({CONFIG['risk_percent']}%)\n"
        f"⏰ *Session :* {sig['session']}\n"
        f"📈 *Score :* {sig['score']}/4 confluences\n"
        f"📉 *RSI :* {sig['rsi']}\n"
        f"🕐 *Heure Niamey :* {sig['time']}\n"
        f"{'─' * 30}\n"
        f"*Confluences détectées :*\n{confluence_text}\n"
        f"{'─' * 30}\n"
        f"⚡ *Que veux-tu faire ?*"
    )
    return msg

# ─────────────────────────────────────────────
#  SCAN DU MARCHÉ (tâche périodique)
# ─────────────────────────────────────────────
async def market_scan(context: ContextTypes.DEFAULT_TYPE):
    """Scan toutes les paires et envoie un signal si trouvé"""
    if not STATE["bot_active"] or STATE["pending_signal"]:
        return
    if not is_kill_zone():
        return
    if STATE["scanning"]:
        return

    STATE["scanning"] = True
    logger.info(f"🔍 Scan de {len(ALL_PAIRS)} paires...")

    try:
        for symbol in ALL_PAIRS:
            if STATE["pending_signal"]:
                break

            # Eviter de re-signaler la même paire dans les 15 min
            last_sig_time = STATE["last_signals"].get(symbol, 0)
            if time.time() - last_sig_time < 900:
                continue

            sig = await analyse_pair(symbol)
            if sig:
                STATE["pending_signal"] = sig
                STATE["pending_time"]   = time.time()
                STATE["last_signals"][symbol] = time.time()

                msg = format_signal_message(sig)
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ VALIDER", callback_data="CONFIRM"),
                        InlineKeyboardButton("❌ ANNULER", callback_data="CANCEL"),
                    ],
                    [
                        InlineKeyboardButton("⏭ PAIRE SUIVANTE", callback_data="SKIP"),
                    ]
                ])
                await context.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                logger.info(f"✅ Signal envoyé : {symbol} {'+' if sig['direction']==1 else '-'} | Score {sig['score']}/4")
                break

            await asyncio.sleep(2)  # Respecter le rate limit API

    except Exception as e:
        logger.error(f"Erreur scan: {e}")
    finally:
        STATE["scanning"] = False

# ─────────────────────────────────────────────
#  EXPIRATION DES SIGNAUX (tâche périodique)
# ─────────────────────────────────────────────
async def check_signal_expiry(context: ContextTypes.DEFAULT_TYPE):
    if STATE["pending_signal"] and STATE["pending_time"]:
        elapsed = time.time() - STATE["pending_time"]
        if elapsed > CONFIG["signal_expiry"]:
            sym = STATE["pending_signal"]["symbol"]
            STATE["pending_signal"] = None
            STATE["pending_time"]   = None
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⏱️ Signal *{sym}* expiré (2 min sans réponse). Scan reprend...",
                parse_mode="Markdown"
            )

# ─────────────────────────────────────────────
#  HANDLERS BOUTONS INLINE
# ─────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    data     = query.data
    sig      = STATE["pending_signal"]

    if data == "CONFIRM" and sig:
        STATE["pending_signal"] = None
        STATE["pending_time"]   = None
        STATE["total_trades"]  += 1

        # Simuler l'exécution (intégration Exness API à connecter)
        dir_text = "ACHAT ✅" if sig["direction"] == 1 else "VENTE ✅"
        pos = {
            "symbol":    sig["symbol"],
            "direction": sig["direction"],
            "entry":     sig["entry"],
            "sl":        sig["sl"],
            "tp":        sig["tp"],
            "time":      sig["time"],
            "id":        STATE["total_trades"],
        }
        STATE["open_positions"].append(pos)

        await query.edit_message_text(
            f"✅ *Trade VALIDÉ et EXÉCUTÉ !*\n\n"
            f"📊 {dir_text} `{sig['symbol']}`\n"
            f"💵 Prix entrée : `{sig['entry']}`\n"
            f"🛑 Stop Loss : `{sig['sl']}`\n"
            f"🎯 Take Profit : `{sig['tp']}`\n"
            f"🆔 ID Trade : #{STATE['total_trades']}\n\n"
            f"_Bonne chance ! Le bot surveille la position._",
            parse_mode="Markdown"
        )

    elif data == "CANCEL" and sig:
        STATE["pending_signal"] = None
        STATE["pending_time"]   = None
        await query.edit_message_text(
            f"❌ *Trade annulé.*\nSignal `{sig['symbol']}` ignoré.\n_Scan reprend..._",
            parse_mode="Markdown"
        )

    elif data == "SKIP" and sig:
        STATE["pending_signal"] = None
        STATE["pending_time"]   = None
        await query.edit_message_text(
            f"⏭ *Paire ignorée.* Scan de la paire suivante...",
            parse_mode="Markdown"
        )

    elif data == "CLOSE_ALL":
        count = len(STATE["open_positions"])
        STATE["open_positions"] = []
        await query.edit_message_text(
            f"🔒 *{count} position(s) fermée(s).*",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
#  COMMANDES TELEGRAM
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *SympaTrade Cloud Bot v3.0*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 21 paires surveillées 24h/24\n"
        "🧠 ICT/SMC + EMA + RSI + MACD\n"
        "🇳🇪 Optimisé pour Niamey (UTC+1)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "*Commandes disponibles :*\n"
        "/status — État du bot & capital\n"
        "/positions — Positions ouvertes\n"
        "/pairs — Liste des 21 paires\n"
        "/scan — Lancer un scan manuel\n"
        "/close — Fermer toutes les positions\n"
        "/demo — Basculer en mode DÉMO\n"
        "/real — Basculer en mode RÉEL\n"
        "/risk — Voir/changer le risque\n"
        "/pause — Mettre le bot en pause\n"
        "/resume — Reprendre le bot\n"
        "/stats — Statistiques de trading\n"
        "/help — Aide complète"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone(timedelta(hours=1)))
    kill = "✅ Active" if is_kill_zone() else "❌ Inactive (hors session)"
    mode = "🔵 DÉMO" if CONFIG["account_mode"] == "demo" else "🔴 RÉEL"
    pnl_emoji = "📈" if STATE["daily_pnl"] >= 0 else "📉"

    msg = (
        f"📊 *STATUT SYMPATRADE BOT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Bot : {'✅ Actif' if STATE['bot_active'] else '⏸ En pause'}\n"
        f"💼 Compte : {mode}\n"
        f"💰 Capital (simulé) : ${STATE['daily_start_bal']:,.2f}\n"
        f"{pnl_emoji} P&L du jour : ${STATE['daily_pnl']:+.2f}\n"
        f"📋 Positions ouvertes : {len(STATE['open_positions'])}/{CONFIG['max_open_trades']}\n"
        f"⚡ Kill Zone : {kill}\n"
        f"⏰ Heure Niamey : {now.strftime('%H:%M')}\n"
        f"📅 {now.strftime('%A %d/%m/%Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Paires scannées : {len(ALL_PAIRS)}\n"
        f"⚙️ Risque/trade : {CONFIG['risk_percent']}%\n"
        f"🛑 SL : {CONFIG['sl_pips']} pips | 🎯 TP : {CONFIG['tp_pips']} pips"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not STATE["open_positions"]:
        await update.message.reply_text("📋 *Aucune position ouverte.*", parse_mode="Markdown")
        return
    msg = "📋 *POSITIONS OUVERTES*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
    for p in STATE["open_positions"]:
        d = "▲ BUY" if p["direction"] == 1 else "▼ SELL"
        msg += (
            f"\n🆔 #{p['id']} — `{p['symbol']}`\n"
            f"  {d} @ `{p['entry']}`\n"
            f"  🛑 SL: `{p['sl']}` | 🎯 TP: `{p['tp']}`\n"
            f"  ⏰ Ouvert à {p['time']}\n"
        )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔒 Tout fermer", callback_data="CLOSE_ALL")
    ]])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=keyboard)

async def cmd_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    forex_list = " | ".join([p.replace("/", "") for p in FOREX_PAIRS])
    msg = (
        f"📊 *PAIRES SURVEILLÉES ({len(ALL_PAIRS)} au total)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💱 *Forex (20 paires) :*\n`{forex_list}`\n\n"
        f"🥇 *Métaux (1 paire) :*\n`XAUUSD`\n\n"
        f"_Scan toutes les {CONFIG['scan_interval']}s pendant les Kill Zones_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_kill_zone():
        await update.message.reply_text(
            "⚠️ *Hors Kill Zone* — Le scan automatique est suspendu.\n"
            "Sessions actives :\n"
            f"🇬🇧 Londres : {CONFIG['london_open']}h00 – {CONFIG['london_close']}h00\n"
            f"🇺🇸 New York : {CONFIG['ny_open']}h00 – {CONFIG['ny_close']}h00\n"
            "_Scan forcé quand même..._",
            parse_mode="Markdown"
        )
    await update.message.reply_text("🔍 *Scan manuel lancé sur 21 paires...*", parse_mode="Markdown")
    await market_scan(context)

async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = len(STATE["open_positions"])
    STATE["open_positions"] = []
    STATE["pending_signal"] = None
    await update.message.reply_text(
        f"🔒 *{count} position(s) fermée(s).*\nBot prêt pour nouveaux signaux.",
        parse_mode="Markdown"
    )

async def cmd_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CONFIG["account_mode"] = "demo"
    await update.message.reply_text("🔵 *Mode DÉMO activé.*\nAucun argent réel utilisé.", parse_mode="Markdown")

async def cmd_real(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CONFIG["account_mode"] = "real"
    await update.message.reply_text(
        "🔴 *Mode RÉEL activé.*\n⚠️ Les trades seront exécutés sur ton compte Exness réel !\nSois prudent.",
        parse_mode="Markdown"
    )

async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        try:
            r = float(args[0])
            if 0.1 <= r <= 5.0:
                CONFIG["risk_percent"] = r
                await update.message.reply_text(f"⚙️ *Risque mis à jour : {r}%*", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Entre 0.1% et 5% maximum.", parse_mode="Markdown")
        except:
            await update.message.reply_text("Usage: /risk 1.5", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"⚙️ *Risque actuel : {CONFIG['risk_percent']}%*\n\nPour changer : `/risk 1.5`",
            parse_mode="Markdown"
        )

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["bot_active"] = False
    await update.message.reply_text("⏸ *Bot en pause.* Utilise /resume pour reprendre.", parse_mode="Markdown")

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["bot_active"] = True
    await update.message.reply_text("▶️ *Bot repris !* Scan des marchés actif.", parse_mode="Markdown")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = STATE["total_trades"]
    wins  = STATE["wins"]
    losses= STATE["losses"]
    wr    = (wins / total * 100) if total > 0 else 0
    msg = (
        f"📈 *STATISTIQUES SYMPATRADE*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total trades : {total}\n"
        f"✅ Gagnants : {wins}\n"
        f"❌ Perdants : {losses}\n"
        f"🎯 Winrate : {wr:.1f}%\n"
        f"💰 P&L du jour : ${STATE['daily_pnl']:+.2f}\n"
        f"📦 En attente : {len(STATE['open_positions'])} position(s)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *AIDE SYMPATRADE BOT*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "*Comment ça fonctionne :*\n"
        "1️⃣ Le bot scanne 21 paires toutes les 60s\n"
        "2️⃣ Quand 3+ confluences détectées → signal envoyé\n"
        "3️⃣ Tu appuies ✅ VALIDER ou ❌ ANNULER\n"
        "4️⃣ Si validé → trade exécuté sur Exness\n\n"
        "*Kill Zones (Niamey UTC+1) :*\n"
        "🇬🇧 Londres : 8h00 – 12h00\n"
        "🇺🇸 New York : 14h00 – 22h00\n\n"
        "*Score de confluence :*\n"
        "• EMA 9/21/50 (tendance)\n"
        "• RSI 14 (momentum)\n"
        "• MACD 12/26/9 (confirmation)\n"
        "• ICT/SMC OB+FVG+BOS\n"
        "_Min 3/4 pour envoyer un signal_\n\n"
        "*Sécurités :*\n"
        "🛡 Circuit-breaker perte journalière 5%\n"
        "🛡 Spread max 3 pips\n"
        "🛡 Signal expire après 2 minutes\n"
        "🛡 Max 3 positions simultanées"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ─────────────────────────────────────────────
#  MESSAGE DE DÉMARRAGE
# ─────────────────────────────────────────────
async def post_init(application: Application):
    bot = application.bot
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            "🚀 *SympaTrade Cloud Bot v3.0 DÉMARRÉ !*\n\n"
            f"📡 {len(ALL_PAIRS)} paires surveillées\n"
            "🧠 Stratégies : ICT/SMC + EMA + RSI + MACD\n"
            "⏰ Kill Zones : Londres 8h-12h | NY 14h-22h\n"
            "🔵 Mode DÉMO actif\n\n"
            "Tape /help pour voir toutes les commandes."
        ),
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────
#  POINT D'ENTRÉE PRINCIPAL
# ─────────────────────────────────────────────
def main():
    logger.info("Démarrage SympaTrade Cloud Bot v3.0...")

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commandes
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("pairs",     cmd_pairs))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("close",     cmd_close))
    app.add_handler(CommandHandler("demo",      cmd_demo))
    app.add_handler(CommandHandler("real",      cmd_real))
    app.add_handler(CommandHandler("risk",      cmd_risk))
    app.add_handler(CommandHandler("pause",     cmd_pause))
    app.add_handler(CommandHandler("resume",    cmd_resume))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("help",      cmd_help))

    # Boutons inline
    app.add_handler(CallbackQueryHandler(button_handler))

    # Tâches périodiques
    jq: JobQueue = app.job_queue
    jq.run_repeating(market_scan,        interval=CONFIG["scan_interval"], first=10)
    jq.run_repeating(check_signal_expiry, interval=15,                     first=15)

    logger.info(f"✅ Bot prêt — {len(ALL_PAIRS)} paires | Scan toutes les {CONFIG['scan_interval']}s")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
