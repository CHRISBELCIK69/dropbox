import dropbox
import os
import re
import json
import requests
import time
import threading
from datetime import datetime, timedelta

# ============================================
# CONFIGURATION — all from Railway env vars
# ============================================
DROPBOX_APP_KEY       = os.environ["DROPBOX_APP_KEY"]
DROPBOX_APP_SECRET    = os.environ["DROPBOX_APP_SECRET"]
DROPBOX_REFRESH_TOKEN = os.environ["DROPBOX_REFRESH_TOKEN"]
DROPBOX_FOLDER        = os.environ.get("DROPBOX_FOLDER", "/trades")

TRADIER_TOKEN         = os.environ["TRADIER_TOKEN"]
TRADIER_ACCOUNT       = os.environ["TRADIER_ACCOUNT"]
TRADIER_BASE_URL      = os.environ.get("TRADIER_BASE_URL", "https://sandbox.tradier.com/v1")

OCR_API_KEY           = os.environ["OCR_API_KEY"]
POLL_INTERVAL         = int(os.environ.get("POLL_INTERVAL", "5"))
SAVE_FOLDER           = "/tmp/trades_images"

IMAGE_EXTENSIONS      = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp"}

os.makedirs(SAVE_FOLDER, exist_ok=True)

# ============================================
# DROPBOX
# ============================================
try:
    dbx = dropbox.Dropbox(
        app_key=DROPBOX_APP_KEY,
        app_secret=DROPBOX_APP_SECRET,
        oauth2_refresh_token=DROPBOX_REFRESH_TOKEN
    )
    name = dbx.users_get_current_account().name.display_name
    print(f"✅ Connected to Dropbox as: {name}")
except Exception as e:
    print(f"❌ Dropbox connection failed: {e}")
    exit(1)

processed_files  = set()
active_positions = set()
positions_lock   = threading.Lock()

def list_new_images():
    result = dbx.files_list_folder(DROPBOX_FOLDER)
    return [
        e for e in result.entries
        if isinstance(e, dropbox.files.FileMetadata)
        and os.path.splitext(e.name.lower())[1] in IMAGE_EXTENSIONS
        and e.path_display not in processed_files
    ]

def download_file(dropbox_path, filename):
    save_path = os.path.join(SAVE_FOLDER, filename)
    _, response = dbx.files_download(dropbox_path)
    with open(save_path, "wb") as f:
        f.write(response.content)
    print(f"✅ Downloaded: {filename}")
    return save_path

# ============================================
# OCR
# ============================================
def ocr_image(image_path):
    print(f"🔍 Running OCR on: {os.path.basename(image_path)}")
    with open(image_path, "rb") as f:
        resp = requests.post(
            "https://api.ocr.space/parse/image",
            headers={"apikey": OCR_API_KEY},
            files={"file": f},
            data={
                "language":          "eng",
                "isOverlayRequired": "false",
                "detectOrientation": "false",
                "scale":             "true",
                "OCREngine":         "2"
            },
            timeout=30
        )
    resp.raise_for_status()
    result = resp.json()
    if result.get("IsErroredOnProcessing"):
        print(f"❌ OCR error: {result.get('ErrorMessage')}")
        return None
    parsed = result.get("ParsedResults", [])
    if not parsed:
        return None
    text = parsed[0].get("ParsedText", "").strip()
    return text if text else None

# ============================================
# CONTRACT PARSING
# ============================================
TICKERS = {
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT", "AMZN",
    "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B", "C",
    "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS",
    "CVX", "DE", "DHR", "DIS", "DUK", "EMR", "FDX", "GD", "GE", "GILD",
    "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC", "INTU", "ISRG",
    "JNJ", "JPM", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD", "MDLZ",
    "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE", "NFLX",
    "NKE", "NOW", "NVDA", "ORCL", "PEP", "PFE", "PG", "PLTR", "PM", "PYPL",
    "QCOM", "RTX", "SBUX", "SCHW", "SO", "SPG", "SPX", "SPY", "T", "TGT",
    "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ",
    "WFC", "WMT", "XOM", "QQQ", "IWM", "DIA", "VOO", "VTI", "AGG", "GLD",
    "TLT", "HYG", "EEM"
}

def parse_contracts(text):
    text = ' '.join(text.upper().split())
    pattern_tickers = r'(?:' + '|'.join(map(re.escape, TICKERS)) + r')'
    patterns = [
        rf'\b({pattern_tickers})\s*(\d+(?:\.\d+)?)\s*([CP])\b',
        rf'\b({pattern_tickers})\s*\$?\s*(\d+(?:\.\d+)?)\s*([CP])\b',
        rf'\b({pattern_tickers})\s*(\d+(?:\.\d+)?)\s*(CALL|PUT)',
    ]
    results = set()
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            ticker = m.group(1).upper()
            strike = m.group(2)
            cp     = m.group(3).upper()
            if cp == "CALL": cp = "C"
            if cp == "PUT":  cp = "P"
            try:
                if 0.5 <= float(strike) <= 10000:
                    results.add((ticker, strike, cp))
            except ValueError:
                pass
    return list(results)

def get_next_friday():
    today = datetime.now()
    days  = (4 - today.weekday()) % 7
    if days == 0:
        days = 7
    return (today + timedelta(days=days)).strftime("%Y-%m-%d")

def to_occ_symbol(ticker, strike, cp, expiry):
    dt          = datetime.strptime(expiry, "%Y-%m-%d")
    expiry_code = dt.strftime("%y%m%d")
    strike_code = f"{int(round(float(strike) * 1000)):08d}"
    return f"{ticker.upper()}{expiry_code}{cp.upper()}{strike_code}"

def format_contracts(contracts):
    expiry = get_next_friday()
    return [{
        "ticker":     ticker,
        "strike":     float(strike),
        "type":       "Call" if cp == "C" else "Put",
        "expiry":     expiry,
        "occ_symbol": to_occ_symbol(ticker, strike, cp, expiry),
        "readable":   f"{ticker} {strike} {'Call' if cp == 'C' else 'Put'} exp {expiry}"
    } for ticker, strike, cp in contracts]

# ============================================
# TRADIER
# ============================================
tradier_session = requests.Session()
tradier_session.headers.update({
    "Authorization": f"Bearer {TRADIER_TOKEN}",
    "Accept":        "application/json"
})

placed_orders = set()

def place_order(contract):
    symbol = contract["occ_symbol"]
    ticker = contract["ticker"]

    if symbol in placed_orders:
        print(f"⏭️  Already ordered: {symbol}")
        return False

    print(f"\n🚀 Placing order: {contract['readable']}")
    try:
        resp = tradier_session.post(
            f"{TRADIER_BASE_URL}/accounts/{TRADIER_ACCOUNT}/orders",
            data={
                "class":         "option",
                "symbol":        ticker,
                "option_symbol": symbol,
                "side":          "buy_to_open",
                "quantity":      "1",
                "type":          "market",
                "duration":      "day"
            }
        )
        resp.raise_for_status()
        result = resp.json().get("order", {})
        print(f"✅ Order placed — ID: {result.get('id')}  Status: {result.get('status')}")
        placed_orders.add(symbol)
        return True
    except Exception as e:
        print(f"❌ Order failed: {e}")
        return False

def get_option_history(option_symbol, days=14):
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)
    resp = tradier_session.get(
        f"{TRADIER_BASE_URL}/markets/history",
        params={
            "symbol":   option_symbol,
            "interval": "daily",
            "start":    start_date.strftime("%Y-%m-%d"),
            "end":      end_date.strftime("%Y-%m-%d"),
        }
    )
    resp.raise_for_status()
    history = resp.json().get("history", {})
    if not history or history == "null":
        return []
    days_data = history.get("day", [])
    return days_data if isinstance(days_data, list) else [days_data]

def get_current_price(option_symbol):
    resp = tradier_session.get(
        f"{TRADIER_BASE_URL}/markets/quotes",
        params={"symbols": option_symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    quote = resp.json().get("quotes", {}).get("quote", {})
    return float(quote.get("last") or quote.get("ask") or quote.get("bid") or 0)

def sell_asset(option_symbol, underlying, quantity):
    resp = tradier_session.post(
        f"{TRADIER_BASE_URL}/accounts/{TRADIER_ACCOUNT}/orders",
        data={
            "class":         "option",
            "symbol":        underlying,
            "option_symbol": option_symbol,
            "side":          "sell_to_close",
            "quantity":      str(int(quantity)),
            "type":          "market",
            "duration":      "day"
        }
    )
    resp.raise_for_status()
    result = resp.json().get("order", {})
    print(f"🔴 SOLD — ID: {result.get('id')}  Status: {result.get('status')}")

# ============================================
# ATR TRAILING STOP
# ============================================
def assign_stop(GAP, entry, option_symbol, underlying, quantity):
    initial_stop  = round(entry - GAP, 2)
    highest_price = entry
    trailing_stop = initial_stop
    print(f"[{option_symbol}] Initial stop: {initial_stop}  (GAP={GAP})")

    while True:
        try:
            current_price = get_current_price(option_symbol)

            if current_price == 0:
                print(f"[{option_symbol}] Price unavailable — waiting")
                time.sleep(30)
                continue

            if current_price > highest_price:
                highest_price = current_price
                trailing_stop = round(highest_price - GAP, 2)
                print(f"[{option_symbol}] Stop raised to: {trailing_stop}  (high={highest_price})")

            print(f"[{option_symbol}] Price: {current_price}  |  Stop: {trailing_stop}")

            if current_price <= trailing_stop:
                sell_asset(option_symbol, underlying, quantity)
                with positions_lock:
                    active_positions.discard(option_symbol)
                break

        except Exception as e:
            print(f"[{option_symbol}] Error: {e}")

        time.sleep(5)

def start_atr_monitor(option_symbol, entry, quantity):
    underlying = re.match(r'^([A-Z]+)', option_symbol).group(1)

    bars = get_option_history(option_symbol, days=14)
    if not bars:
        print(f"[{option_symbol}] No history — skipping ATR")
        return

    true_ranges = []
    for bar in bars:
        tr = max(
            bar["high"] - bar["low"],
            abs(bar["high"] - bar["close"]),
            abs(bar["close"] - bar["low"])
        )
        true_ranges.append(round(tr, 2))

    atr     = round(sum(true_ranges) / len(true_ranges), 2)
    GAP     = atr
    max_gap = round(entry * 0.30, 2)

    if GAP > max_gap:
        print(f"[{option_symbol}] GAP {GAP} clamped to 30% → {max_gap}")
        GAP = max_gap

    print(f"[{option_symbol}] entry={entry}  ATR={atr}  GAP={GAP}  stop={round(entry - GAP, 2)}")

    with positions_lock:
        active_positions.add(option_symbol)

    t = threading.Thread(
        target=assign_stop,
        args=(GAP, entry, option_symbol, underlying, quantity),
        daemon=True
    )
    t.start()

# ============================================
# IMAGE PROCESSING PIPELINE
# ============================================
def process_image(entry):
    print(f"\n📸 New image: {entry.name}")

    local_path = download_file(entry.path_display, entry.name)

    text = ocr_image(local_path)
    if not text:
        print("⚠️  No text extracted")
        return

    print(f"\n📄 OCR Text:\n{'-'*30}\n{text}\n{'-'*30}")

    contracts = parse_contracts(text)
    if not contracts:
        print("⚠️  No contracts found")
        return

    formatted = format_contracts(contracts)

    for contract in formatted:
        print(f"\n🎯 {contract['readable']}  OCC: {contract['occ_symbol']}")
        success = place_order(contract)

        if success:
            time.sleep(2)
            start_atr_monitor(
                option_symbol=contract["occ_symbol"],
                entry=contract["strike"],
                quantity=1
            )

# ============================================
# POLLING LOOP
# ============================================
def poll():
    print(f"\n👀 Polling Dropbox every {POLL_INTERVAL}s")
    print(f"📁 Folder: {DROPBOX_FOLDER}\n")

    while True:
        try:
            new_images = list_new_images()
            if new_images:
                for entry in new_images:
                    processed_files.add(entry.path_display)
                    t = threading.Thread(target=process_image, args=(entry,), daemon=True)
                    t.start()
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting...", end="\r")
        except Exception as e:
            print(f"❌ Poll error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    poll()


