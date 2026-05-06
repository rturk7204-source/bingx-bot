#!/usr/bin/env python3
"""Еженедельный auto-tuner: прогоняет бэктест с 3 ATR-порогами и шлёт отчёт в Телеграм."""
import os, sys, subprocess, asyncio
sys.path.insert(0, "/root/bingx-bot")
os.chdir("/root/bingx-bot")

def run(atr_min):
    src = open("assistant/signals/trade_calc.py").read()
    orig = src
    new = src.replace("ATR_MIN = 0.4", f"ATR_MIN = {atr_min}").replace("ATR_MIN = 0.5", f"ATR_MIN = {atr_min}").replace("ATR_MIN = 0.3", f"ATR_MIN = {atr_min}")
    open("assistant/signals/trade_calc.py","w").write(new)
    try:
        out = subprocess.run(["python3","backtest.py"], capture_output=True, text=True, timeout=900).stdout
    finally:
        open("assistant/signals/trade_calc.py","w").write(orig)
    res = {}
    for line in out.split("\n"):
        if "сделок:" in line: res["n"] = line.split(":")[1].strip()
        elif "WR:" in line and "avg" in line:
            parts = line.replace("\t"," ").split()
            for i,p in enumerate(parts):
                if p == "WR:": res["wr"] = parts[i+1]
                if p == "R:" and i>0 and parts[i-1]=="sum": res["sumR"] = parts[i+1]
                if p == "R:" and i>0 and parts[i-1]=="avg": res["avgR"] = parts[i+1]
                if "maxDD:" in p: res["dd"] = parts[i+1] if i+1<len(parts) else "?"
    return res

print("Прогон: ATR=0.3, 0.4, 0.5...")
results = {x: run(x) for x in [0.3, 0.4, 0.5]}

lines = ["📊 ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ AUTO-TUNER", "", "ATR | сделок | WR | avgR | sumR | DD"]
for atr, r in results.items():
    lines.append(f"{atr} | {r.get('n','?')} | {r.get('wr','?')} | {r.get('avgR','?')} | {r.get('sumR','?')} | {r.get('dd','?')}")
best = max(results.items(), key=lambda x: float((x[1].get("sumR","0").replace("+","")) or 0))
lines.append("")
lines.append(f"🏆 Лучший: ATR_MIN={best[0]} (sumR={best[1].get('sumR')})")
lines.append(f"📌 Текущий в боте: ATR_MIN=0.4")
lines.append("Меняй вручную если хочешь — авто-замены НЕТ.")

text = "\n".join(lines)
print(text)

# Отправка в Телеграм
import aiohttp
async def send():
    from assistant.core import config as _cfg
    url = f"https://api.telegram.org/bot{_cfg.TG_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json={"chat_id": _cfg.TG_CHAT_ID, "text": text}) as r:
            print("tg:", r.status)
asyncio.run(send())
