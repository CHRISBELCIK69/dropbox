import dropbox
import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ============================================
# CONFIGURATION
# ============================================
DROPBOX_APP_KEY       = os.environ["DROPBOX_APP_KEY"]
DROPBOX_APP_SECRET    = os.environ["DROPBOX_APP_SECRET"]
DROPBOX_REFRESH_TOKEN = os.environ["DROPBOX_REFRESH_TOKEN"]
DROPBOX_FOLDER        = os.environ.get("DROPBOX_FOLDER", "/trades")

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

# ============================================
# CLEAR FOLDER
# ============================================
def clear_dropbox_folder():
    print("🗑️  Clearing Dropbox trades folder...")
    try:
        result = dbx.files_list_folder(DROPBOX_FOLDER)
        deleted = 0
        for entry in result.entries:
            if isinstance(entry, dropbox.files.FileMetadata):
                dbx.files_delete_v2(entry.path_display)
                print(f"   Deleted: {entry.name}")
                deleted += 1
        print(f"✅ Cleared {deleted} file(s) from {DROPBOX_FOLDER}")
    except Exception as e:
        print(f"❌ Failed to clear Dropbox: {e}")

# ============================================
# SCHEDULER
# ============================================
def run():
    while True:
        now = datetime.now(ZoneInfo("America/New_York"))

        if now.weekday() <= 4:
            market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

            # If already past 4pm today push to tomorrow
            if now >= market_close:
                market_close += timedelta(days=1)
                # Skip to Monday if that lands on weekend
                while market_close.weekday() > 4:
                    market_close += timedelta(days=1)

            wait_seconds = (market_close - now).total_seconds()
            h = int(wait_seconds // 3600)
            m = int((wait_seconds % 3600) // 60)
            print(f"🕓 Next cleanup at 4pm ET — in {h}h {m}m")
            time.sleep(wait_seconds)
            clear_dropbox_folder()

        else:
            # Weekend — check again in an hour
            print(f"📅 Weekend — checking again in 1 hour")
            time.sleep(3600)

if __name__ == "__main__":
    run()
```

Save it as `cleanup.py` and add a second worker in your `Procfile`:
```
worker: python main.py
worker2: python cleanup.py
