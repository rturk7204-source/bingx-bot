"""
Бэктест полного пайплайна с BE+trailing.
Запуск: cd /root/bingx-bot && python3 backtest.py [days=30] [interval=15m] [universe=50] [--ablate=mtf|btc|depth|none]
"""
import sys, statistics, time as _time
from collections import defaultdict

from assistant.core import exchange
from assistant.collectors.market import get_universe
from assistant.analysis import klines as klines_mod
from assistant.analysis.klines import _normalize
from assistant.analysis.scoring import score_candidate
from assistant.signals.trade_calc import calc_trade

# параметры
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
INTERVAL = sys.argv[2] if len(sys.argv) > 2 else "15m"
UNI_LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 50
ABLATE = None
for a in sys.argv:
    if a.startswith("--ablate="):
        ABLATE = a.split("=",1)[1]

TF_SEC = {"15m":900, "30m":1800, "1h":3600, "4h":14400}.get(INTERVAL, 900)
BARS_PER_DAY = 86400 // TF_SEC
total_bars = BARS_PER_DAY * DAYS

print(f"=== БЭКТЕСТ {DAYS}д {INTERVAL} ({total_bars} баров) {UNI_LIMIT} монет  ablate={ABLATE} ===\n")

def load(sym, tf, limit):
    r = exchange.get_klines(sym, tf, min(limit, 1000))
    if r.get("code") != 0: return []
    return _normalize(r.get("data", []))

# ABLATION: подменим quality в зависимости от ABLATE
if ABLATE:
    from assistant.analysis import quality as _q
    _orig_check = _q.check_quality
    def patched(symbol, direction, entry, sl, tp, interval="15m"):
        ok, reason, info = _orig_check(symbol, direction, entry, sl, tp, interval)
        if not ok and reason:
            r_low = reason.lower()
            if ABLATE == "mtf" and ("ema50" in r_low or "1h тренд" in r_low):
                return True, None, info
            if ABLATE == "btc" and "btc " in r_low:
                return True, None, info
            if ABLATE == "depth" and "плита" in r_low:
                return True, None, info
        return ok, reason, info
    _q.check_quality = patched

uni = get_universe(limit=UNI_LIMIT)
print(f"вселенная: {len(uni)}\nзагружаю историю...")
buffer = {}
for u in uni:
    sym = u["symbol"]
    K15 = load(sym, "15m", total_bars + 200)
    K1h = load(sym, "1h", total_bars // 4 + 200)
    if K15: buffer[(sym, "15m")] = K15
    if K1h: buffer[(sym, "1h")] = K1h
btc = load("BTC-USDT", "1h", total_bars // 4 + 200)
if btc: buffer[("BTC-USDT", "1h")] = btc
print(f"загружено: {len(buffer)} серий\n")

# Симулятор позиции с BE+trailing
def simulate_trade(K, i, direction, entry, sl, tp, max_bars=80):
    """Возвращает (result, pnl_R, bars, hit_BE, hit_trail_step)"""
    R = abs(entry - sl)
    if R <= 0: return ("invalid", 0, 0, False, 0)
    cur_sl = sl
    be_done = False
    trail_step = 0
    future = K[i+1:i+1+max_bars]
    for j, fk in enumerate(future):
        bars = j+1
        # текущая прибыль в R по close (для BE/trailing решений)
        # но SL/TP проверяем по high/low
        # 1) проверка стопа/тейка
        if direction == "LONG":
            hit_sl = fk["l"] <= cur_sl
            hit_tp = fk["h"] >= tp
            if hit_sl:
                pnl_R = (cur_sl - entry) / R
                tag = "BE" if be_done and abs(cur_sl - entry)/R < 0.05 else ("TRAIL" if be_done else "SL")
                return (tag, pnl_R, bars, be_done, trail_step)
            if hit_tp:
                return ("TP", 3.0, bars, be_done, trail_step)
        else:
            hit_sl = fk["h"] >= cur_sl
            hit_tp = fk["l"] <= tp
            if hit_sl:
                pnl_R = (entry - cur_sl) / R
                tag = "BE" if be_done and abs(cur_sl - entry)/R < 0.05 else ("TRAIL" if be_done else "SL")
                return (tag, pnl_R, bars, be_done, trail_step)
            if hit_tp:
                return ("TP", 3.0, bars, be_done, trail_step)
        # 2) после высокого/низкого бара двигаем SL
        peak = (fk["h"] - entry)/R if direction == "LONG" else (entry - fk["l"])/R
        # +1R → BE
        if not be_done and peak >= 1.0:
            cur_sl = entry  # БУ
            be_done = True
        # +2R и далее каждые +0.5R
        if be_done and peak >= 2.0:
            target = int((peak - 1.5) // 0.5)  # 1 при +2R
            if target > trail_step:
                trail_step = target
                offset = (target * 0.5 + 0.5) * R
                cur_sl = entry + offset if direction == "LONG" else entry - offset

    # вышли по таймауту
    last = future[-1]["c"] if future else entry
    pnl_R = (last - entry)/R if direction == "LONG" else (entry - last)/R
    return ("timeout", pnl_R, len(future), be_done, trail_step)

# прогон
trades = []
total_signals = 0
quality_pass = 0

for u in uni:
    sym = u["symbol"]
    K = buffer.get((sym, "15m"), [])
    if len(K) < 200: continue
    for i in range(100, len(K) - 80):
        klines_mod.set_backtest(buffer, K[i]["t"])
        u_now = dict(u)
        u_now["lastPrice"] = K[i]["c"]
        u_now["last_price"] = K[i]["c"]
        recent = K[max(0,i-95):i+1]
        if recent:
            u_now["highPrice"] = max(k["h"] for k in recent)
            u_now["lowPrice"] = min(k["l"] for k in recent)
        try: s_ = score_candidate(u_now)
        except: s_ = None
        if not s_ or s_.get("score",0) < 30: continue
        total_signals += 1
        try: p_ = calc_trade(s_, 1500)
        except: continue
        if "error" in p_: continue
        if not p_.get("quality_ok", True): continue
        quality_pass += 1

        result, pnl_R, bars, be, trail = simulate_trade(
            K, i, p_["direction"], p_["entry"], p_["sl"], p_["tp"]
        )
        trades.append({
            "sym": sym, "dir": p_["direction"],
            "result": result, "R": pnl_R, "bars": bars,
            "be": be, "trail": trail,
            "tag": s_.get("detail", {}).get("smc", "")
        })

klines_mod.clear_backtest()

print(f"score≥30: {total_signals}")
print(f"прошло quality: {quality_pass}")
print(f"сделок: {len(trades)}\n")

if not trades:
    print("нет сделок"); sys.exit(0)

wins = [t for t in trades if t["R"] > 0.001]
avg_R = statistics.mean(t["R"] for t in trades)
sum_R = sum(t["R"] for t in trades)
wr = len(wins)/len(trades)*100

eq=peak=max_dd=0
for t in trades:
    eq += t["R"]
    if eq>peak: peak=eq
    if peak-eq > max_dd: max_dd = peak-eq

by_result = defaultdict(int)
for t in trades: by_result[t["result"]] += 1

print(f"WR: {wr:.1f}%   avg R: {avg_R:+.2f}   sum R: {sum_R:+.1f}   maxDD: {max_dd:.1f}R")
print(f"avg длительность: {statistics.mean(t['bars'] for t in trades):.0f} баров")
print(f"BE сработал: {sum(1 for t in trades if t['be'])}/{len(trades)}")
print(f"trail срабатывал: {sum(1 for t in trades if t['trail'])} ")
print(f"\nрезультаты: {dict(by_result)}")
print(f"\nPnL при $15 риска: ${sum_R*15:+.0f}")

by_tag = defaultdict(lambda: {"n":0,"R":0,"w":0})
for t in trades:
    d = by_tag[t["tag"] or "—"]
    d["n"] += 1; d["R"] += t["R"]
    if t["R"] > 0.001: d["w"] += 1
print("\nпо setup_tag:")
for tag,d in sorted(by_tag.items(), key=lambda x:-x[1]["R"]):
    print(f"  {tag:<30} n={d['n']:>4}  WR={d['w']/d['n']*100:>4.0f}%  sumR={d['R']:+.1f}  avgR={d['R']/d['n']:+.2f}")
