from flask import Flask, jsonify, render_template_string, request, session, redirect
try:
    from flask_cors import CORS
except:
    CORS = None
import subprocess
import json
import os
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = "bingx_bot_secret_2026"
if CORS:
    CORS(app, resources={r"/public/*": {"origins": "*"}})
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "bingx2026")

LOGIN_HTML = open("/root/bingx-bot/login_template.html").read()

from functools import wraps
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>BingX Trading Bot Dashboard</title>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="30">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', sans-serif; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; font-size: 24px; }
        h2 { color: #58a6ff; margin-bottom: 10px; font-size: 16px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; }
        .card .value { font-size: 24px; font-weight: bold; margin-top: 5px; }
        .card .label { color: #8b949e; font-size: 12px; }
        .green { color: #3fb950; }
        .red { color: #f85149; }
        .yellow { color: #e3b341; }
        .blue { color: #58a6ff; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #21262d; padding: 8px 12px; text-align: left; font-size: 12px; color: #8b949e; }
        td { padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }
        tr:hover { background: #161b22; }
        .log-box { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 15px; height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; }
        .log-line { margin-bottom: 4px; }
        .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
        .status-active { background: #3fb950; }
        .status-inactive { background: #f85149; }
        .refresh { color: #8b949e; font-size: 12px; float: right; }
    </style>
</head>
<body>
    <h1>🤖 BingX Trading Bot <span class="refresh">Обновление каждые 30 сек</span></h1>

    <div style="margin-bottom:15px">
        <button onclick="botAction('start')" style="background:#238636;color:white;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;margin-right:10px;font-size:14px">&#9654; Запустить</button>
        <button onclick="botAction('stop')" style="background:#da3633;color:white;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;margin-right:10px;font-size:14px">&#9646; Остановить</button>
        <button onclick="botAction('restart')" style="background:#1f6feb;color:white;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px">&#8635; Перезапустить</button>
        <a href="/logout" style="background:#30363d;color:#e6edf3;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:14px;text-decoration:none;margin-left:10px">🚪 Выйти</a>
        <span id="msg" style="margin-left:15px;color:#8b949e;font-size:13px"></span>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    function botAction(a){
        document.getElementById('msg').innerText='Выполняется...';
        fetch('/api/bot/'+a,{method:'POST'}).then(r=>r.json()).then(d=>{
            document.getElementById('msg').innerText=d.message;
            setTimeout(()=>location.reload(),2000);
        });
    }
    </script>
    <div class="grid">
        <div class="card">
            <div class="label">Баланс</div>
            <div class="value blue">{{ balance }} USDT</div>
        </div>
        <div class="card">
            <div class="label">Equity</div>
            <div class="value">{{ equity }} USDT</div>
        </div>
        <div class="card">
            <div class="label">Unrealized PnL</div>
            <div class="value {{ 'green' if upnl >= 0 else 'red' }}">{{ "%+.2f"|format(upnl) }} USDT</div>
        </div>
        <div class="card">
            <div class="label">Доступная маржа</div>
            <div class="value">{{ margin }} USDT</div>
        </div>
        <div class="card">
            <div class="label">Всего сделок</div>
            <div class="value blue">{{ stats.total_trades }}</div>
        </div>
        <div class="card">
            <div class="label">Win Rate</div>
            <div class="value {{ 'green' if stats.win_rate >= 50 else 'red' }}">{{ stats.win_rate }}%</div>
        </div>
        <div class="card">
            <div class="label">Общая прибыль</div>
            <div class="value {{ 'green' if stats.total_profit >= 0 else 'red' }}">{{ "%+.2f"|format(stats.total_profit) }}%</div>
        </div>
        <div class="card">
            <div class="label">Статус бота</div>
            <div class="value">
                <span class="status-dot {{ 'status-active' if bot_active else 'status-inactive' }}"></span>
                {{ 'Активен' if bot_active else 'Остановлен' }}
            </div>
        </div>
    </div>

    <div class="section">
        <h2>📊 Открытые позиции</h2>
        {% if positions %}
        <table>
            <tr><th>Пара</th><th>Сторона</th><th>Объём</th><th>Вход</th><th>Тек. цена</th><th>PnL USDT</th><th>PnL %</th><th>Плечо</th><th>Ликвидация</th></tr>
            {% for p in positions %}
            {% set entry = p.avgPrice|float %}
            {% set mark = p.markPrice|float %}
            {% set side = p.positionSide %}
            {% set pnl_pct = ((mark-entry)/entry*100) if side=='LONG' else ((entry-mark)/entry*100) %}
            <tr>
                <td><b>{{ p.symbol }}</b></td>
                <td class="{{ 'green' if side == 'LONG' else 'red' }}"><b>{{ side }}</b></td>
                <td>{{ p.positionAmt }}</td>
                <td>{{ p.avgPrice }}</td>
                <td>{{ p.markPrice }}</td>
                <td class="{{ 'green' if p.unrealizedProfit|float >= 0 else 'red' }}"><b>{{ "%+.4f"|format(p.unrealizedProfit|float) }}</b></td>
                <td class="{{ 'green' if pnl_pct >= 0 else 'red' }}"><b>{{ "%+.2f"|format(pnl_pct) }}%</b></td>
                <td style="color:#e3b341">{{ p.leverage }}x</td>
                <td style="color:#f85149">{{ p.liquidationPrice }}</td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <p style="color:#8b949e">Нет открытых позиций</p>
        {% endif %}
    </div>

        <h2>⚡ Скальпинг</h2>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">
            <div style="background:#0d1117;border-radius:8px;padding:12px;text-align:center">
                <div style="color:#8b949e;font-size:11px;margin-bottom:4px">СДЕЛОК</div>
                <div style="font-size:20px;font-weight:700;color:#e3b341">{{ scalp_stats.count }}</div>
            </div>
            <div style="background:#0d1117;border-radius:8px;padding:12px;text-align:center">
                <div style="color:#8b949e;font-size:11px;margin-bottom:4px">WIN RATE</div>
                <div style="font-size:20px;font-weight:700;color:{{ '#3fb950' if scalp_stats.win_rate >= 50 else '#f85149' }}">{{ scalp_stats.win_rate }}%</div>
            </div>
            <div style="background:#0d1117;border-radius:8px;padding:12px;text-align:center">
                <div style="color:#8b949e;font-size:11px;margin-bottom:4px">TOTAL PnL</div>
                <div style="font-size:20px;font-weight:700;color:{{ '#3fb950' if scalp_stats.total_pnl >= 0 else '#f85149' }}">{{ "%+.2f"|format(scalp_stats.total_pnl) }}%</div>
            </div>
            <div style="background:#0d1117;border-radius:8px;padding:12px;text-align:center">
                <div style="color:#8b949e;font-size:11px;margin-bottom:4px">СЕССИИ</div>
                <div style="font-size:11px;color:#e3b341;margin-top:4px">London 07:30-09:00</div>
                <div style="font-size:11px;color:#e3b341">NY 12:00-14:00</div>
                <div style="font-size:11px;color:#e3b341">NY 15:00-17:00</div>
            </div>
        </div>
    </div>
    <div class="section">
        <h2>📊 Прибыль по парам</h2>
        {% if pair_stats %}
        <table>
            <tr><th>Пара</th><th>Сделок</th><th>Побед</th><th>Win Rate</th><th>Total PnL</th><th>Статус</th></tr>
            {% for p in pair_stats %}
            <tr>
                <td><b>{{ p.symbol }}</b></td>
                <td style="color:#8b949e">{{ p.total }}</td>
                <td class="green">{{ p.wins }}</td>
                <td class="{{ 'green' if p.win_rate >= 50 else 'red' }}"><b>{{ p.win_rate }}%</b></td>
                <td class="{{ 'green' if p.total_pnl >= 0 else 'red' }}"><b>{{ "%+.2f"|format(p.total_pnl) }}%</b></td>
                <td>
                    {% if p.win_rate >= 55 %}
                    <span style="color:#3fb950">🔥 Отлично</span>
                    {% elif p.win_rate >= 50 %}
                    <span style="color:#e3b341">✓ Норма</span>
                    {% else %}
                    <span style="color:#f85149">⚠ Слабая</span>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <p style="color:#8b949e">Недостаточно данных</p>
        {% endif %}
    </div>

    <div class="section">
        <h2>📈 История сделок (последние 20)</h2>
        {% if trades %}
        <table>
            <tr><th>Время</th><th>Пара</th><th>Сторона</th><th>Цена</th><th>Объём</th><th>PnL</th><th>Причина</th></tr>
            {% for t in trades[-20:]|reverse %}
            <tr>
                <td style="color:#8b949e">{{ t.get("timestamp","")[:16] }}</td>
                <td><b>{{ t.symbol }}</b></td>
                <td class="{{ 'green' if t.side == 'BUY' else 'red' }}">{{ t.side }}</td>
                <td>{{ t.price }}</td>
                <td>{{ t.quantity }}</td>
                <td class="{{ 'green' if t.get('pnl',0) >= 0 else 'red' }}">
                    {{ "%+.2f"|format(t.get("pnl",0)) + "%" if "pnl" in t else "-" }}
                </td>
                <td>
                    {% set r = t.reason %}
                    {% if r == "Trailing Stop" %}
                    <span style="color:#58a6ff">{{ r }}</span>
                    {% elif "Fib" in r %}
                    <span style="color:#bc8cff">{{ r }}</span>
                    {% elif r == "Partial TP" %}
                    <span style="color:#e3b341">{{ r }}</span>
                    {% elif "Stop-Loss" in r %}
                    <span style="color:#f85149">{{ r }}</span>
                    {% else %}
                    {{ r }}
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <p style="color:#8b949e">Нет сделок</p>
        {% endif %}
    </div>

    <div class="section">
        <h2>📋 Последние логи</h2>
        <div class="log-box" id="logs">
            {% for line in logs %}
            <div class="log-line">{{ line }}</div>
            {% endfor %}
        </div>
    </div>
    <div class="section" id="chart-section">
        <h2>График баланса</h2>
        <div id="chartContainer" style="position:relative;height:200px">
            <canvas id="balanceChart"></canvas>
        </div>
    </div>

    <script>
    // График баланса
    fetch('/api/balance_history').then(r=>r.json()).then(data=>{
        if(!data.labels) return;
        new Chart(document.getElementById('balanceChart'), {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [{
                    label: 'Баланс USDT',
                    data: data.values,
                    borderColor: '#58a6ff',
                    backgroundColor: 'rgba(88,166,255,0.1)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { labels: { color: '#e6edf3' } } },
                scales: {
                    x: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
                    y: { ticks: { color: '#8b949e' }, grid: { color: '#21262d' } }
                }
            }
        });
    });
    </script>
</body>
</html>
"""

def get_balance():
    try:
        from bingx_api import BingXAPI
        api = BingXAPI(os.getenv("BINGX_API_KEY"), os.getenv("BINGX_SECRET_KEY"))
        result = api.get_balance()
        if result and isinstance(result, dict):
            inner = result.get("balance", {})
            if isinstance(inner, dict):
                return inner
        return {}
    except:
        return {}

def get_positions():
    try:
        from bingx_api import BingXAPI
        api = BingXAPI(os.getenv("BINGX_API_KEY"), os.getenv("BINGX_SECRET_KEY"))
        positions = api.get_positions()
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]
    except:
        return []

def get_scalp_stats():
    try:
        with open("/root/bingx-bot/trades.json") as f:
            trades = json.load(f)
        scalp_trades = [t for t in trades if "Scalp" in t.get("reason","")]
        wins = sum(1 for t in scalp_trades if t.get("pnl",0) > 0)
        total_pnl = sum(t.get("pnl",0) for t in scalp_trades)
        wr = round(wins/len(scalp_trades)*100,1) if scalp_trades else 0
        return {"count": len(scalp_trades), "wins": wins,
                "win_rate": wr, "total_pnl": round(total_pnl,2)}
    except: return {"count":0,"wins":0,"win_rate":0,"total_pnl":0}

def get_pair_stats():
    try:
        with open("/root/bingx-bot/trades.json") as f:
            trades = json.load(f)
        from collections import defaultdict
        stats = defaultdict(lambda: {"wins":0,"losses":0,"total":0,"pnl":0.0})
        for t in trades:
            sym = t.get("symbol","")
            pnl = t.get("pnl")
            if pnl is None: continue
            pnl = float(pnl)
            stats[sym]["total"] += 1
            stats[sym]["pnl"] = round(stats[sym]["pnl"] + pnl, 2)
            if pnl > 0: stats[sym]["wins"] += 1
            else: stats[sym]["losses"] += 1
        result = []
        for sym, s in stats.items():
            wr = round(s["wins"]/s["total"]*100, 1) if s["total"] > 0 else 0
            result.append({"symbol":sym,"total":s["total"],"wins":s["wins"],
                          "losses":s["losses"],"win_rate":wr,"total_pnl":s["pnl"]})
        result.sort(key=lambda x: x["total_pnl"], reverse=True)
        return result
    except: return []

def get_trades():
    try:
        with open("/root/bingx-bot/trades.json") as f:
            return json.load(f)
    except:
        return []

def get_stats():
    try:
        sys.path.insert(0, "/root/bingx-bot")
        from analytics import Analytics
        a = Analytics()
        return a.get_stats()
    except:
        return {"total_trades": 0, "win_rate": 0, "total_profit": 0, "best_trade": 0, "worst_trade": 0}

def get_logs():
    try:
        result = subprocess.run(
            ["journalctl", "-u", "bingx-bot", "-n", "50", "--no-pager", "-o", "short"],
            capture_output=True, text=True
        )
        lines = result.stdout.strip().split("\n")
        return [l[l.find("python3"):] if "python3" in l else l for l in lines[-30:]]
    except:
        return []

def is_bot_active():
    try:
        result = subprocess.run(["systemctl", "is-active", "bingx-bot"], capture_output=True, text=True)
        return result.stdout.strip() == "active"
    except:
        return False

import sys
sys.path.insert(0, "/root/bingx-bot")

@app.route("/")
@login_required
def dashboard():
    bal = get_balance()
    stats = type("Stats", (), get_stats())()
    positions = get_positions()
    trades = get_trades()
    pair_stats = get_pair_stats()
    scalp_stats = get_scalp_stats()
    logs = get_logs()
    bot_active = is_bot_active()

    return render_template_string(HTML,
        balance=float(bal.get("balance", 0)),
        equity=float(bal.get("equity", 0)),
        upnl=float(bal.get("unrealizedProfit", 0)),
        margin=float(bal.get("availableMargin", 0)),
        stats=stats,
        positions=positions,
        trades=trades,
        pair_stats=pair_stats,
        scalp_stats=scalp_stats,
        logs=logs,
        bot_active=bot_active
    )

@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(get_stats())

@app.route("/api/balance")
@login_required
def api_balance():
    return jsonify(get_balance())


@app.route("/api/bot/<action>", methods=["POST"])
@login_required
def bot_control(action):
    import subprocess
    cmds = {"start":["systemctl","start","bingx-bot"],"stop":["systemctl","stop","bingx-bot"],"restart":["systemctl","restart","bingx-bot"]}
    msgs = {"start":"Bot started","stop":"Bot stopped","restart":"Bot restarted"}
    if action in cmds:
        subprocess.run(cmds[action])
        return jsonify({"message": msgs[action]})
    return jsonify({"message": "Unknown action"})


import json as _json

BALANCE_HISTORY_FILE = '/root/bingx-bot/balance_history.json'

@app.route('/api/balance_history')
@login_required
def balance_history():
    try:
        if os.path.exists(BALANCE_HISTORY_FILE):
            with open(BALANCE_HISTORY_FILE) as f:
                history = _json.load(f)
            return jsonify({'labels': [h['time'] for h in history], 'values': [h['balance'] for h in history]})
    except:
        pass
    return jsonify({'labels': [], 'values': []})



# ===== PUBLIC API для Lovable дашборда =====
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response

@app.route("/public/stats")
def public_stats():
    # Перечитываем trades.json каждый раз
    try:
        import json
        trades = json.load(open('/root/bingx-bot/trades.json'))
        pnls = [float(t['pnl']) for t in trades if t.get('pnl')]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p <= 0)
        pnl_usdt = []
        for t in trades:
            if t.get('pnl'):
                usdt = float(t['pnl']) / 100 * float(t.get('quantity',0)) * float(t.get('price',0))
                pnl_usdt.append(usdt)
        return jsonify({
            'total_trades': len(pnls),
            'win_rate': round(wins/len(pnls)*100, 1) if pnls else 0,
            'total_profit_pct': round(sum(pnls), 2),
            'total_profit': round(sum(pnl_usdt), 2),
            'avg_profit': round(sum(pnls)/len(pnls), 2) if pnls else 0,
            'best_trade': round(max(pnls), 2) if pnls else 0,
            'worst_trade': round(min(pnls), 2) if pnls else 0,
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route("/public/balance")
def public_balance():
    return jsonify(get_balance())

@app.route("/public/balance_history")
def public_balance_history():
    try:
        if os.path.exists(BALANCE_HISTORY_FILE):
            with open(BALANCE_HISTORY_FILE) as f:
                history = _json.load(f)
            return jsonify({"labels": [h["time"] for h in history], "values": [h["balance"] for h in history]})
    except:
        pass
    return jsonify({"labels": [], "values": []})

@app.route("/public/trades")
def public_trades():
    try:
        import json
        trades = json.load(open('/root/bingx-bot/trades.json'))
        return jsonify(trades[-200:])  # последние 200
    except:
        return jsonify([])

@app.route("/public/positions")
def public_positions():
    try:
        import sys
        sys.path.insert(0, "/root/bingx-bot")
        from bingx_api import BingXAPI
        from dotenv import load_dotenv
        load_dotenv()
        api = BingXAPI(api_key=os.getenv("BINGX_API_KEY"), secret_key=os.getenv("BINGX_SECRET_KEY"))
        positions = api.get_positions()
        active = [p for p in positions if abs(float(p.get("positionAmt", 0))) > 0]
        return jsonify(active)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/public/status")
def public_status():
    import subprocess
    result = subprocess.run(["systemctl", "is-active", "bingx-bot"], capture_output=True, text=True)
    return jsonify({"status": result.stdout.strip(), "active": result.stdout.strip() == "active"})

# ===== END PUBLIC API =====

@app.route("/login", methods=["GET", "POST"])
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
