"""
Backtest 8-условного классического ICT/SMC по ТЗ (smc-bot-summary).

Логика (все AND):
  1. SSL/BSL swept на 4h (equal H/L pool)
  2. Kill Zone: 07-09, 13-15, 20-21 UTC
  3. Premium/Discount: LONG только в нижней половине 4h-range последних 20 свечей
  4. BOS на 4h в направлении
  5. Свежий нетронутый 4h OB (mitigated=False)
  6. CHoCH на 1h внутри 4h-OB
  7. FVG на 15m в направлении
  8. RR >= 3.0 до противоположного пула ликвидности
SL: за 4h OB
TP: противоположный equal-high/low pool на 4h
BE: после +1R стоп в безубыток
Mitigated: OB мёртв после первого касания (одноразовый)
Без трейла, без частичных, 1 трейд = 1R риск

Прогон: top-20 альтов BingX, 90 дней, 4h как опорный тайм.
"""
import sys, os, time, json
from datetime import datetime, timezone
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex

# ============== CONFIG ==============
DAYS = 90
TOP_N = 20
KILL_ZONES_UTC = [(7,9),(13,15),(20,21)]
MIN_RR = 3.0
PREMIUM_DISCOUNT_LOOKBACK = 20  # 4h candles
OB_LOOKBACK_4H = 30   # сколько 4h свечей назад искать OB
SWING_LEFT = 2; SWING_RIGHT = 2

# ============== KLINE FETCH ==============
def fetch_klines_range(sym, interval, end_ts_ms, total_needed):
    """Качает свечи назад от end_ts с пагинацией."""
    out = []
    cur_end = end_ts_ms
    remaining = total_needed
    while remaining > 0:
        limit = min(1000, remaining)
        r = ex.request("GET","/openApi/swap/v3/quote/klines",
            {"symbol":sym,"interval":interval,"limit":limit,"endTime":cur_end}, auth=False)
        data = r.get("data") or []
        if not data: break
        batch = []
        for k in data:
            if isinstance(k, dict):
                batch.append({"t":int(k["time"]),"o":float(k["open"]),"h":float(k["high"]),"l":float(k["low"]),"c":float(k["close"]),"v":float(k.get("volume",0))})
            else:
                batch.append({"t":int(k[0]),"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),"v":float(k[5])})
        batch.sort(key=lambda x:x["t"])
        if not batch: break
        out = batch + out
        cur_end = batch[0]["t"] - 1
        remaining -= len(batch)
        if len(batch) < limit*0.5: break
        time.sleep(0.1)
    # uniq + sort
    seen = set(); uniq = []
    for k in out:
        if k["t"] in seen: continue
        seen.add(k["t"]); uniq.append(k)
    uniq.sort(key=lambda x:x["t"])
    return uniq

# ============== SMC PRIMITIVES ==============
def swings(K, left=SWING_LEFT, right=SWING_RIGHT):
    out = []
    for i in range(left, len(K)-right):
        h = K[i]["h"]; l = K[i]["l"]
        if all(K[i-j-1]["h"] < h for j in range(left)) and all(K[i+j+1]["h"] < h for j in range(right)):
            out.append((i, h, "H"))
        if all(K[i-j-1]["l"] > l for j in range(left)) and all(K[i+j+1]["l"] > l for j in range(right)):
            out.append((i, l, "L"))
    return out

def equal_levels(sw, tol_pct=0.15):
    """Группируем близкие swings (equal H/L) — это пулы ликвидности."""
    highs = sorted([(i,p) for i,p,t in sw if t=="H"], key=lambda x:x[1])
    lows  = sorted([(i,p) for i,p,t in sw if t=="L"], key=lambda x:x[1])
    pools_h = []  # bsl
    pools_l = []  # ssl
    # H pools
    cur = []
    for i,p in highs:
        if not cur or abs(p - cur[-1][1])/cur[-1][1]*100 <= tol_pct:
            cur.append((i,p))
        else:
            if len(cur)>=2: pools_h.append({"price":sum(x[1] for x in cur)/len(cur),"hits":len(cur),"last_idx":max(x[0] for x in cur)})
            cur=[(i,p)]
    if len(cur)>=2: pools_h.append({"price":sum(x[1] for x in cur)/len(cur),"hits":len(cur),"last_idx":max(x[0] for x in cur)})
    cur=[]
    for i,p in lows:
        if not cur or abs(p - cur[-1][1])/cur[-1][1]*100 <= tol_pct:
            cur.append((i,p))
        else:
            if len(cur)>=2: pools_l.append({"price":sum(x[1] for x in cur)/len(cur),"hits":len(cur),"last_idx":max(x[0] for x in cur)})
            cur=[(i,p)]
    if len(cur)>=2: pools_l.append({"price":sum(x[1] for x in cur)/len(cur),"hits":len(cur),"last_idx":max(x[0] for x in cur)})
    return pools_h, pools_l

def detect_bos(K, sw, lookback=10):
    """BOS на последних свечах: пробой последнего HH (LONG) или LL (SHORT) лимитированным lookback."""
    if not sw or len(K)<5: return None
    last_close = K[-1]["c"]
    older = [s for s in sw if s[0] <= len(K)-2]
    if not older: return None
    last_h = next((s for s in reversed(older) if s[2]=="H"), None)
    last_l = next((s for s in reversed(older) if s[2]=="L"), None)
    if last_h and last_close > last_h[1] and (len(K)-1 - last_h[0]) <= lookback:
        return {"dir":"LONG","level":last_h[1],"swing_idx":last_h[0]}
    if last_l and last_close < last_l[1] and (len(K)-1 - last_l[0]) <= lookback:
        return {"dir":"SHORT","level":last_l[1],"swing_idx":last_l[0]}
    return None

def find_ob(K, event, max_back=20):
    """OB = последняя противоположная свеча до импульса."""
    if not event: return None
    si = event["swing_idx"]
    for i in range(si, max(si-max_back,0), -1):
        if i >= len(K): continue
        k = K[i]
        if event["dir"]=="LONG" and k["c"]<k["o"]:
            return {"high":k["h"],"low":k["l"],"idx":i}
        if event["dir"]=="SHORT" and k["c"]>k["o"]:
            return {"high":k["h"],"low":k["l"],"idx":i}
    return None

def is_ob_mitigated(K_after, ob, direction):
    """OB mitigated если цена коснулась зоны хотя бы раз ПОСЛЕ его формирования."""
    if not K_after: return False
    for k in K_after:
        if direction=="LONG":
            if k["l"] <= ob["high"]:
                return True
        else:
            if k["h"] >= ob["low"]:
                return True
    return False

def fvg_in_range(K_15m, direction, t_from, t_to):
    """FVG на 15m в окне времени и в направлении."""
    for i in range(1, len(K_15m)-1):
        if K_15m[i]["t"] < t_from or K_15m[i]["t"] > t_to: continue
        if direction=="LONG" and K_15m[i+1]["l"] > K_15m[i-1]["h"]:
            return {"top":K_15m[i+1]["l"], "bot":K_15m[i-1]["h"], "t":K_15m[i]["t"]}
        if direction=="SHORT" and K_15m[i+1]["h"] < K_15m[i-1]["l"]:
            return {"top":K_15m[i-1]["l"], "bot":K_15m[i+1]["h"], "t":K_15m[i]["t"]}
    return None

def in_kill_zone(ts_ms):
    h = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).hour
    return any(a <= h < b for a,b in KILL_ZONES_UTC)

def premium_discount(K4h, ref_idx, direction):
    """Цена в discount (LONG) или premium (SHORT) относительно range последних N 4h-свечей."""
    start = max(0, ref_idx - PREMIUM_DISCOUNT_LOOKBACK)
    rng = K4h[start:ref_idx+1]
    if len(rng) < 5: return False
    hi = max(k["h"] for k in rng); lo = min(k["l"] for k in rng)
    eq = (hi+lo)/2
    price = K4h[ref_idx]["c"]
    if direction=="LONG": return price < eq
    return price > eq

def ssl_bsl_swept(K4h, ref_idx, direction, pools_h, pools_l):
    """Сняли ли противоположный пул ликвидности недавно (за последние 8 4h-свечей)."""
    win = K4h[max(0,ref_idx-8):ref_idx+1]
    if direction=="LONG":
        # должен быть свежий sweep SSL pool
        for p in pools_l:
            if p["last_idx"] < ref_idx-30: continue
            for k in win:
                if k["l"] < p["price"] and k["c"] > p["price"]:
                    return True, p
        return False, None
    else:
        for p in pools_h:
            if p["last_idx"] < ref_idx-30: continue
            for k in win:
                if k["h"] > p["price"] and k["c"] < p["price"]:
                    return True, p
        return False, None

def opposite_pool(direction, pools_h, pools_l, entry):
    """Ближайший противоположный пул для TP."""
    if direction=="LONG":
        above = [p for p in pools_h if p["price"] > entry]
        if not above: return None
        return min(above, key=lambda p: p["price"])
    else:
        below = [p for p in pools_l if p["price"] < entry]
        if not below: return None
        return max(below, key=lambda p: p["price"])

# ============== BACKTEST ENGINE ==============
def backtest_symbol(sym, K4h, K1h, K15m):
    """Симулирует ICT/SMC по ТЗ. Возвращает список трейдов."""
    trades = []
    used_obs = set()  # (idx, side) — mitigated OB
    # Идём по 4h свечам начиная с минимума истории
    start = 50  # нужен буфер для swings/pools
    for i in range(start, len(K4h)-1):
        K4h_slice = K4h[:i+1]
        sw4 = swings(K4h_slice)
        if len(sw4) < 6: continue
        pools_h, pools_l = equal_levels(sw4)
        event = detect_bos(K4h_slice, sw4, lookback=8)
        if not event: continue
        ob = find_ob(K4h_slice, event)
        if not ob: continue
        ob_key = (ob["idx"], event["dir"])
        if ob_key in used_obs: continue
        # OB не должен быть mitigated между формированием и текущим моментом
        K_after_ob = K4h_slice[ob["idx"]+1 : i]  # исключая текущую
        if is_ob_mitigated(K_after_ob, ob, event["dir"]):
            used_obs.add(ob_key); continue

        # Premium/Discount
        if not premium_discount(K4h_slice, i, event["dir"]): continue

        # SSL/BSL swept
        swept, pool = ssl_bsl_swept(K4h_slice, i, event["dir"], pools_h, pools_l)
        if not swept: continue

        # Kill zone (на момент тек 4h свечи закрытия)
        if not in_kill_zone(K4h[i]["t"]): continue

        # 1h CHoCH внутри OB зоны за последние 4 часа
        t_from = K4h[i]["t"] - 4*3600*1000
        t_to   = K4h[i]["t"] + 4*3600*1000
        K1h_win = [k for k in K1h if t_from <= k["t"] <= t_to]
        if len(K1h_win) < 3: continue
        sw1 = swings(K1h_win, 1, 1)
        choch_ok = False
        if event["dir"]=="LONG":
            last_h1 = next((s for s in reversed(sw1) if s[2]=="H"), None)
            if last_h1 and K1h_win[-1]["c"] > last_h1[1]: choch_ok = True
        else:
            last_l1 = next((s for s in reversed(sw1) if s[2]=="L"), None)
            if last_l1 and K1h_win[-1]["c"] < last_l1[1]: choch_ok = True
        # Hot fix: CHoCH должен быть внутри 4h-OB зоны
        if choch_ok:
            last_1h_price = K1h_win[-1]["c"]
            if not (ob["low"] <= last_1h_price <= ob["high"]):
                # цена не в зоне OB — пропускаем
                if event["dir"]=="LONG" and last_1h_price > ob["high"]: pass  # вышла за зону, пропуск
                else: choch_ok = False
        if not choch_ok: continue

        # 15m FVG в окне ±2h
        t_from15 = K4h[i]["t"] - 2*3600*1000
        t_to15   = K4h[i]["t"] + 4*3600*1000
        fvg = fvg_in_range(K15m, event["dir"], t_from15, t_to15)
        if not fvg: continue

        # Entry = FVG midpoint
        entry = (fvg["top"] + fvg["bot"]) / 2

        # SL за OB на 4h (+0.1% буфер)
        if event["dir"]=="LONG":
            sl = ob["low"] * 0.999
        else:
            sl = ob["high"] * 1.001

        R = abs(entry - sl)
        if R <= 0: continue

        # TP = противоположный пул
        tp_pool = opposite_pool(event["dir"], pools_h, pools_l, entry)
        if not tp_pool: continue
        tp = tp_pool["price"]
        rr = abs(tp - entry) / R
        if rr < MIN_RR: continue

        # Симуляция исхода: идём по 15m свечам после FVG до развязки
        be_active = False
        outcome_R = None
        # Берём 15m с момента FVG вперёд, лимит 96 свечей = 24 часа
        start_15m = next((j for j,k in enumerate(K15m) if k["t"] >= fvg["t"]), None)
        if start_15m is None: continue
        for j in range(start_15m, min(start_15m+96*5, len(K15m))):
            k = K15m[j]
            # сначала проверяем достижение entry (для лимитного входа)
            if k["l"] <= entry <= k["h"]:
                # вход состоялся, мониторим этот же бар на SL/TP/BE
                cur_sl = sl
                # симулируем оставшийся путь начиная с этого бара
                hit = None
                for jj in range(j, min(j+96*5, len(K15m))):
                    kk = K15m[jj]
                    # для LONG: SL low first vs TP high
                    if event["dir"]=="LONG":
                        # порядок: предположим консервативно - сначала SL потом TP
                        sl_hit = kk["l"] <= cur_sl
                        tp_hit = kk["h"] >= tp
                        be_hit = (not be_active) and kk["h"] >= entry + R
                        if sl_hit and tp_hit:
                            # неоднозначно, считаем -1R или 0R если BE
                            outcome_R = 0 if be_active else -1
                            hit = "sl"; break
                        if sl_hit:
                            outcome_R = 0 if be_active else -1
                            hit = "sl"; break
                        if tp_hit:
                            outcome_R = rr
                            hit = "tp"; break
                        if be_hit:
                            cur_sl = entry; be_active = True
                    else:
                        sl_hit = kk["h"] >= cur_sl
                        tp_hit = kk["l"] <= tp
                        be_hit = (not be_active) and kk["l"] <= entry - R
                        if sl_hit and tp_hit:
                            outcome_R = 0 if be_active else -1
                            hit = "sl"; break
                        if sl_hit:
                            outcome_R = 0 if be_active else -1
                            hit = "sl"; break
                        if tp_hit:
                            outcome_R = rr
                            hit = "tp"; break
                        if be_hit:
                            cur_sl = entry; be_active = True
                if hit is None:
                    outcome_R = 0  # таймаут
                break

        if outcome_R is None:
            continue  # вход не сработал в окне

        used_obs.add(ob_key)
        trades.append({
            "symbol": sym, "dir": event["dir"], "t": K4h[i]["t"],
            "entry": entry, "sl": sl, "tp": tp, "rr": rr,
            "outcome_R": outcome_R, "ob": ob, "pool_tp": tp_pool["price"],
        })
    return trades

# ============== UNIVERSE ==============
def get_top_alts():
    r = ex.request("GET","/openApi/swap/v2/quote/ticker",{},auth=False)
    data = r.get("data") or []
    arr = []
    for t in data:
        sym = t.get("symbol","")
        if not sym.endswith("-USDT"): continue
        if sym.split("-")[0] in {"BTC","ETH"}: continue  # пропускаем мажоров
        try:
            qv = float(t.get("quoteVolume","0"))
        except: qv = 0
        arr.append((sym, qv))
    arr.sort(key=lambda x:-x[1])
    return [s for s,_ in arr[:TOP_N]]

# ============== MAIN ==============
def main():
    end_ts = int(time.time()*1000)
    bars_4h = DAYS*6 + 60
    bars_1h = DAYS*24 + 100
    bars_15m = DAYS*96 + 200

    symbols = get_top_alts()
    print(f"[universe] top-{len(symbols)} alts: {symbols[:10]}...")
    all_trades = []
    for idx, sym in enumerate(symbols):
        try:
            print(f"\n[{idx+1}/{len(symbols)}] {sym}: fetching...")
            K4 = fetch_klines_range(sym, "4h", end_ts, bars_4h)
            K1 = fetch_klines_range(sym, "1h", end_ts, bars_1h)
            K15= fetch_klines_range(sym, "15m", end_ts, bars_15m)
            print(f"  bars: 4h={len(K4)} 1h={len(K1)} 15m={len(K15)}")
            if len(K4)<60 or len(K1)<100 or len(K15)<300: 
                print("  skip — мало данных")
                continue
            tr = backtest_symbol(sym, K4, K1, K15)
            print(f"  trades: {len(tr)}")
            for t in tr[:3]:
                ts = datetime.fromtimestamp(t["t"]/1000, tz=timezone.utc).strftime("%m-%d %H:%M")
                print(f"    {ts} {t['dir']} entry={t['entry']:.4f} sl={t['sl']:.4f} tp={t['tp']:.4f} RR={t['rr']:.1f} → {t['outcome_R']:+.2f}R")
            all_trades.extend(tr)
        except Exception as e:
            print(f"  err: {e}")

    # ============== STATS ==============
    print(f"\n{'='*60}\n[RESULTS] {DAYS} days, {len(symbols)} symbols")
    print(f"Total trades: {len(all_trades)}")
    if not all_trades:
        print("Нет трейдов — критерии слишком жёсткие или нет данных.")
        with open("/tmp/bingx-bot/backtest_smc_classic_out.json","w") as f:
            json.dump({"trades":[], "stats":{}}, f)
        return

    wins = [t for t in all_trades if t["outcome_R"] > 0]
    losses = [t for t in all_trades if t["outcome_R"] < 0]
    breakevens = [t for t in all_trades if t["outcome_R"] == 0]
    total_R = sum(t["outcome_R"] for t in all_trades)
    avg_R = total_R / len(all_trades)
    wr = len(wins)/len(all_trades)*100
    pf = sum(t["outcome_R"] for t in wins) / abs(sum(t["outcome_R"] for t in losses)) if losses else 999
    # max DD
    eq = 0; peak = 0; mdd = 0
    for t in sorted(all_trades, key=lambda x:x["t"]):
        eq += t["outcome_R"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    avg_rr_won = sum(t["rr"] for t in wins)/len(wins) if wins else 0
    
    print(f"  Wins: {len(wins)}  Losses: {len(losses)}  BE: {len(breakevens)}")
    print(f"  Winrate: {wr:.1f}%")
    print(f"  Total R: {total_R:+.2f}")
    print(f"  Avg R/trade: {avg_R:+.2f}")
    print(f"  Profit factor: {pf:.2f}")
    print(f"  Max drawdown: {mdd:.2f}R")
    print(f"  Avg RR on wins: {avg_rr_won:.2f}")
    # per symbol
    print(f"\n[per symbol]")
    by_sym = {}
    for t in all_trades:
        by_sym.setdefault(t["symbol"], []).append(t["outcome_R"])
    for sym, arr in sorted(by_sym.items(), key=lambda x:-sum(x[1])):
        print(f"  {sym}: {len(arr)} trades  total={sum(arr):+.2f}R  wr={sum(1 for x in arr if x>0)/len(arr)*100:.0f}%")
    # save
    with open("/tmp/bingx-bot/backtest_smc_classic_out.json","w") as f:
        json.dump({"trades":all_trades, "stats":{
            "n":len(all_trades),"wr":wr,"total_R":total_R,"avg_R":avg_R,"pf":pf,"mdd":mdd
        }}, f, default=str)
    print(f"\n[saved] /tmp/bingx-bot/backtest_smc_classic_out.json")

if __name__ == "__main__":
    main()
