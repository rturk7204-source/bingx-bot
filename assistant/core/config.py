import os
from dotenv import load_dotenv
load_dotenv("/root/bingx-bot/.env")
BINGX_API_KEY=os.environ["BINGX_API_KEY"]
BINGX_SECRET_KEY=os.environ["BINGX_SECRET_KEY"]
BINGX_BASE="https://open-api.bingx.com"
TG_TOKEN=os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN","")
TG_CHAT_ID=os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID","")
RISK_PCT=1.0
LEVERAGE_BTC=10; LEVERAGE_ETH=8; LEVERAGE_ALT=3
LIQ_BUFFER_PCT=30
MIN_VOLUME_USD=5_000_000; MIN_MCAP_USD=5_000_000
MAX_SPREAD_PCT=0.5; MIN_AGE_DAYS=30; FUNDING_BLACKOUT_MIN=15
SCORE_THRESHOLD=7; TF_CONFIRM=True
DB_PATH="/root/bingx-bot/assistant/data/trading.db"
LOG_DIR="/root/bingx-bot/assistant/logs"
WEIGHT_TECHNICAL=1.0; WEIGHT_SMC=1.5; WEIGHT_VOLUME_PROFILE=1.2; WEIGHT_WYCKOFF=1.3; WEIGHT_CONTEXT=0.8
