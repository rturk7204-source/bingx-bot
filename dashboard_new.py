from flask import Flask, jsonify, render_template_string, request, session, redirect
import subprocess, json, os, sys
from functools import wraps
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = "bingx_secret_2026_xK9"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "bingx2026")
sys.path.insert(0, "/root/bingx-bot")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def get_balance():
    try:
        from bingx_api import BingXAPI
        api = BingXAPI(os.getenv("BINGX_API_KEY"), os.getenv("BINGX_SECRET_KEY"))
        r = api.get_balance()
        return r.get("balance", {}) if r and isinstance(r, dict) else {}
    except: return {}

def get_positions():
    try:
        from bingx_api import BingXAPI
        api = BingXAPI(os.getenv("BINGX_API_KEY"), os.getenv("BINGX_SECRET_KEY"))
        positions = [p for p in api.get_positions() if float(p.get("positionAmt",0)) != 0]
        for p in positions:
            try:
                entry = float(p.get("avgPrice",0))
                mark = float(p.get("markPrice",0))
                side = p.get("positionSide","LONG")
                if entry > 0:
                    p["pnl_pct"] = round(((mark-entry)/entry*100) if side=="LONG" else ((entry-mark)/entry*100), 2)
                    p["sl_price"] = round(entry*0.97 if side=="LONG" else entry*1.03, 5)
                    p["trail_price"] = round(entry*1.015 if side=="LONG" else entry*0.985, 5)
                else:
                    p["pnl_pct"] = 0; p["sl_price"] = 0; p["trail_price"] = 0
            except: p["pnl_pct"]=0; p["sl_price"]=0; p["trail_price"]=0
        return positions
    except: return []

def get_trades():
    try:
        with open("/root/bingx-bot/trades.json") as f: return json.load(f)
    except: return []

def get_stats():
    try:
        from analytics import Analytics
        return Analytics().get_stats()
    except: return {"total_trades":0,"win_rate":0,"total_profit":0,"best_trade":0,"worst_trade":0}

def get_logs():
    try:
        r = subprocess.run(["journalctl","-u","bingx-bot","-n","60","--no-pager","-o","short"],
            capture_output=True, text=True)
        lines = r.stdout.strip().split("\n")[-40:]
        result = []
        for l in lines:
            cls = ""
            if "TRAIL" in l: cls = "log-trail"
            elif "SIGNAL" in l and "BUY" in l: cls = "log-buy"
            elif "SIGNAL" in l and "SELL" in l: cls = "log-sell"
            elif "STOP-LOSS" in l: cls = "log-sl"
            elif "RESTORE" in l: cls = "log-restore"
            elif "AVG" in l: cls = "log-avg"
            elif "Error" in l or "ERROR" in l: cls = "log-error"
            elif "FIB TP" in l or "PARTIAL" in l: cls = "log-tp"
            result.append((l, cls))
        return result
    except: return []

def is_bot_active():
    try:
        r = subprocess.run(["systemctl","is-active","bingx-bot"], capture_output=True, text=True)
        return r.stdout.strip() == "active"
    except: return False

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        error = "Неверный пароль"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/api/biometric-login", methods=["POST"])
def biometric_login():
    session["logged_in"] = True
    return jsonify({"ok": True})

@app.route("/")
@login_required
def dashboard():
    bal = get_balance()
    stats = get_stats()
    positions = get_positions()
    trades = get_trades()
    logs = get_logs()
    bot_active = is_bot_active()
    return render_template_string(DASHBOARD_HTML,
        balance=round(float(bal.get("balance",0)),2),
        equity=round(float(bal.get("equity",0)),2),
        upnl=round(float(bal.get("unrealizedProfit",0)),4),
        margin=round(float(bal.get("availableMargin",0)),2),
        used_margin=round(float(bal.get("usedMargin",0)),2),
        stats=stats, positions=positions, trades=trades,
        logs=logs, bot_active=bot_active)

@app.route("/api/bot/<action>", methods=["POST"])
@login_required
def bot_control(action):
    cmds = {"start":["systemctl","start","bingx-bot"],
            "stop":["systemctl","stop","bingx-bot"],
            "restart":["systemctl","restart","bingx-bot"]}
    msgs = {"start":"Бот запущен","stop":"Бот остановлен","restart":"Бот перезапущен"}
    if action in cmds:
        subprocess.run(cmds[action])
        return jsonify({"message":msgs[action],"ok":True})
    return jsonify({"message":"Unknown","ok":False})

@app.route("/api/balance_history")
@login_required
def balance_history():
    try:
        f = "/root/bingx-bot/balance_history.json"
        if os.path.exists(f):
            with open(f) as fh: h = json.load(fh)
            return jsonify({"labels":[x["time"] for x in h],"values":[x["balance"] for x in h]})
    except: pass
    return jsonify({"labels":[],"values":[]})

@app.route("/api/stats")
@login_required
def api_stats(): return jsonify(get_stats())

@app.route("/api/positions")
@login_required
def api_positions(): return jsonify(get_positions())

LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>BingX Bot</title><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#161b22;border:1px solid #30363d;border-radius:16px;
padding:40px;width:100%;max-width:380px;text-align:center;
box-shadow:0 20px 60px rgba(0,0,0,0.5)}
.logo{font-size:52px;margin-bottom:12px}
h1{color:#58a6ff;font-size:20px;margin-bottom:4px}
.sub{color:#8b949e;font-size:13px;margin-bottom:28px}
input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:8px;
color:#e6edf3;padding:12px 16px;font-size:15px;margin-bottom:14px;
outline:none;transition:border .2s}
input:focus{border-color:#58a6ff}
.btn{width:100%;background:linear-gradient(135deg,#1f6feb,#388bfd);
color:white;border:none;border-radius:8px;padding:13px;font-size:15px;
cursor:pointer;font-weight:600;transition:opacity .2s;margin-bottom:8px}
.btn:hover{opacity:0.9}
.err{color:#f85149;font-size:13px;margin-top:10px;
background:#f8514915;padding:8px 12px;border-radius:6px;border:1px solid #f8514930}
.div{display:flex;align-items:center;gap:10px;margin:18px 0;color:#8b949e;font-size:12px}
.div::before,.div::after{content:'';flex:1;height:1px;background:#30363d}
.bio{width:100%;background:none;border:1px solid #30363d;border-radius:8px;
color:#8b949e;padding:11px;font-size:14px;cursor:pointer;transition:all .2s;
display:flex;align-items:center;justify-content:center;gap:8px}
.bio:hover{border-color:#58a6ff;color:#e6edf3;background:#58a6ff10}
</style></head><body>
<div class="box">
<div class="logo">🤖</div>
<h1>BingX Trading Bot</h1>
<p class="sub">Введите пароль для доступа</p>
<form method="post">
<input type="password" name="password" placeholder="Пароль" autofocus>
<button class="btn" type="submit">🔓 Войти</button>
</form>
{% if error %}<div class="err">⚠️ {{ error }}</div>{% endif %}
<div class="div">или</div>
<button class="bio" onclick="bio()">
<span style="font-size:18px">🔑</span><span>Touch ID / Face ID</span>
</button>
</div>
<script>
async function bio(){
if(!window.PublicKeyCredential){alert('Биометрия не поддерживается в этом браузере');return;}
try{
const c=await navigator.credentials.get({publicKey:{
challenge:crypto.getRandomValues(new Uint8Array(32)),
timeout:60000,userVerification:'required'}});
if(c){const r=await fetch('/api/biometric-login',{method:'POST'});
const d=await r.json();if(d.ok)location.href='/';}
}catch(e){alert('Ошибка биометрии: '+e.message);}
}
</script></body></html>"""
