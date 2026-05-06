import time, hmac, hashlib, requests, urllib.parse
from .config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_BASE
def _sign(p):
    q=urllib.parse.urlencode(sorted(p.items()))
    return q+"&signature="+hmac.new(BINGX_SECRET_KEY.encode(),q.encode(),hashlib.sha256).hexdigest()
def request(m,path,p=None,auth=False,timeout=15):
    p=p or {}
    if auth:
        p["timestamp"]=int(time.time()*1000)
        url=f"{BINGX_BASE}{path}?{_sign(p)}"; h={"X-BX-APIKEY":BINGX_API_KEY}
    else:
        url=f"{BINGX_BASE}{path}"+("?"+urllib.parse.urlencode(p) if p else ""); h={}
    try: return requests.request(m,url,headers=h,timeout=timeout).json()
    except Exception as e: return {"err":str(e)}
def get_ticker(s=None): return request("GET","/openApi/swap/v2/quote/ticker",{"symbol":s} if s else {})
def get_klines(s,i,limit=50): return request("GET","/openApi/swap/v3/quote/klines",{"symbol":s,"interval":i,"limit":limit})
def get_funding(s): return request("GET","/openApi/swap/v2/quote/premiumIndex",{"symbol":s})
def get_open_interest(s): return request("GET","/openApi/swap/v2/quote/openInterest",{"symbol":s})
def get_long_short_ratio(s,i="1h",limit=1): return request("GET","/openApi/swap/v1/quote/longShortRatio",{"symbol":s,"interval":i,"limit":limit})
def get_depth(s,limit=20): return request("GET","/openApi/swap/v2/quote/depth",{"symbol":s,"limit":limit})
def get_positions(s=None): return request("GET","/openApi/swap/v2/user/positions",{"symbol":s} if s else {},auth=True)
def get_balance(): return request("GET","/openApi/swap/v3/user/balance",{},auth=True)
def place_order(**kw): return request("POST","/openApi/swap/v2/trade/order",kw,auth=True)
def set_leverage(s,side,lev): return request("POST","/openApi/swap/v2/trade/leverage",{"symbol":s,"side":side,"leverage":lev},auth=True)
def set_margin_mode(s, mode="ISOLATED"): return request("POST","/openApi/swap/v2/trade/marginType",{"symbol":s,"marginType":mode},auth=True)

def cancel_all_orders(s): return request("DELETE","/openApi/swap/v2/trade/allOpenOrders",{"symbol":s},auth=True)
