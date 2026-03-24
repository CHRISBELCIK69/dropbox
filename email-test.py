import requests
import yagmail
import os
from datetime import datetime

# ============================================
# CONFIGURATION
# ============================================
ACCESS_TOKEN  = os.environ.get("ACCESS_TOKEN", "ejpyU1cstzEqr8L17SGO3GIerzlK")
ACCOUNT_ID    = os.environ.get("ACCOUNT_ID", "VA65780882")
API_BASE_URL  = os.environ.get("API_BASE_URL", "https://sandbox.tradier.com/v1")
EMAIL         = os.environ.get("EMAIL", "chrisbelcik1@gmail.com")
EMAIL_PASS    = os.environ.get("EMAIL_PASS", "qoum ctws nanj ivsi")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept":        "application/json"
})

# ============================================
# FETCH POSITIONS
# ============================================
def get_positions():
    resp = session.get(f"{API_BASE_URL}/accounts/{ACCOUNT_ID}/positions")
    resp.raise_for_status()
    data = resp.json().get("positions", {})
    if not data or data == "null" or isinstance(data, str):
        return []
    positions = data.get("position", [])
    if isinstance(positions, dict):
        positions = [positions]
    return positions or []

def get_quote(symbol):
    resp = session.get(
        f"{API_BASE_URL}/markets/quotes",
        params={"symbols": symbol, "greeks": "false"}
    )
    resp.raise_for_status()
    return resp.json().get("quotes", {}).get("quote", {})

def get_option_history(symbol, days=14):
    from datetime import timedelta
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days)
    resp = session.get(
        f"{API_BASE_URL}/markets/history",
        params={
            "symbol":   symbol,
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

# ============================================
# BUILD REPORT
# ============================================
def build_report():
    positions = get_positions()

    if not positions:
        return "No open positions."

    lines = []
    lines.append(f"📊 POSITION REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("=" * 60)

    for p in positions:
        symbol     = p["symbol"]
        quantity   = p["quantity"]
        cost_basis = p["cost_basis"]
        buy_price  = round(cost_basis / (100 * quantity), 2)

        # Current price
        quote      = get_quote(symbol)
        sell_price = float(quote.get("last") or quote.get("bid") or 0)
        pnl        = round((sell_price - buy_price) * 100 * quantity, 2)
        pnl_pct    = round(((sell_price - buy_price) / buy_price) * 100, 2) if buy_price else 0

        # ATR + stop
        bars = get_option_history(symbol, days=14)
        if bars:
            true_ranges = [
                max(
                    b["high"] - b["low"],
                    abs(b["high"] - b["close"]),
                    abs(b["close"] - b["low"])
                ) for b in bars
            ]
            atr     = round(sum(true_ranges) / len(true_ranges), 2)
            gap     = atr
            max_gap = round(buy_price * 0.30, 2)
            if gap > max_gap:
                gap = max_gap
            stop_loss = round(buy_price - gap, 2)
        else:
            atr       = "N/A"
            gap       = "N/A"
            stop_loss = "N/A"

        lines.append(f"\n🔷 {symbol}")
        lines.append(f"   Quantity   : {quantity}")
        lines.append(f"   Buy Price  : ${buy_price}")
        lines.append(f"   Sell Price : ${sell_price}")
        lines.append(f"   P&L        : ${pnl} ({pnl_pct}%)")
        lines.append(f"   ATR        : {atr}")
        lines.append(f"   Stop Loss  : {stop_loss}")
        lines.append(f"   OPRA       : {symbol}")
        lines.append("-" * 60)

    return "\n".join(lines)

# ============================================
# SEND EMAIL
# ============================================
def send_report():
    print("📊 Fetching positions...")
    body = build_report()
    print(body)

    yag = yagmail.SMTP(EMAIL, EMAIL_PASS)
    yag.send(
        to=EMAIL,
        subject=f"Position Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        contents=body
    )
    print("✅ Email sent")

if __name__ == "__main__":
    send_report()