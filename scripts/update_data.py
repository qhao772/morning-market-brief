"""
Fetches fresh market data from public sources and writes it into data.json.
This script is run once a day by the GitHub Actions workflow at
.github/workflows/update-data.yml — you should not normally need to run it
yourself, but you can (`python scripts/update_data.py`) to test changes.

Sources used:
  - FRED (Federal Reserve Bank of St. Louis) official API: 10yr/30yr
    Treasury yields and the Fed funds target range. Requires a free API key
    (see the FRED_API_KEY note below) — without one, these fields are left
    unchanged from the previous data.json.
  - Yahoo Finance's public quote endpoint: USD/KRW and USD/JPY intraday
    exchange rates (KRW/100 JPY is derived from those two). Free, no key
    needed. (Previously Frankfurter/ECB, which only updates once per weekday.)
  - Google News RSS: top Korean-language "미국 증시" headlines. Free, no key
    needed, but Google will reject requests that look automated, so we send
    a realistic browser User-Agent header.
  - Yahoo Finance's public quote endpoint: KOSPI, KOSDAQ, S&P 500, Nasdaq
    Composite, and the Philadelphia Semiconductor Index (SOX). Free, no key
    needed.

FRED_API_KEY: get a free key at https://fred.stlouisfed.org/docs/api/api_key.html
(just needs an email address), then add it as a repository secret named
FRED_API_KEY (repo Settings > Secrets and variables > Actions > New repository
secret). The workflow passes it to this script as an environment variable.
Until that secret exists, the treasury-yield and Fed-funds fields simply stay
at whatever they were last set to.

Korea's base rate (krBase) is NOT auto-fetched, because there is no reliable
no-key public API for it and it only changes a handful of times a year at
scheduled Bank of Korea meetings. Update it by hand in data.json (the
"krBase" number) whenever the BOK announces a change.
"""

import json
import os
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DATA_FILE = "data.json"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

# A realistic desktop-browser User-Agent. Some services (Google News among
# them) return errors for requests that look like they come from a script
# rather than a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fred_latest(series_id, default=None):
    """Returns the most recent non-missing value for a FRED series using the
    official, documented FRED API. Needs FRED_API_KEY; without it, returns
    `default` unchanged (see module docstring)."""
    if not FRED_API_KEY:
        print(f"[info] FRED_API_KEY not set — keeping previous value for {series_id}")
        return default
    try:
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
            "&sort_order=desc&limit=5"
        )
        payload = json.loads(http_get(url))
        for obs in payload.get("observations", []):
            value = obs.get("value")
            if value and value != ".":
                return float(value)
    except Exception as exc:
        print(f"[warn] fred_latest({series_id}) failed: {exc}")
    return default


YAHOO_FX_SYMBOLS = {
    "usdKrw": "KRW%3DX",
    "usdJpy": "JPY%3DX",
}


YAHOO_INDEX_SYMBOLS = {
    "kospi": "%5EKS11",
    "kosdaq": "%5EKQ11",
    "sp500": "%5EGSPC",
    "nasdaq": "%5EIXIC",
    "sox": "%5ESOX",
}


def yahoo_quote(symbol_encoded):
    """Returns (price, changePercent) for a Yahoo Finance chart-API symbol,
    or (None, None) on any failure."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?range=1d&interval=1d"
        meta = json.loads(http_get(url))["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        change_pct = meta.get("regularMarketChangePercent")
        prev_close = meta.get("chartPreviousClose")
        if change_pct is None and price is not None and prev_close:
            change_pct = (price - prev_close) / prev_close * 100
        return price, change_pct
    except Exception as exc:
        print(f"[warn] yahoo_quote({symbol_encoded}) failed: {exc}")
        return None, None


def fetch_indices(prev_indices):
    indices = {}
    for key, symbol in YAHOO_INDEX_SYMBOLS.items():
        price, change_pct = yahoo_quote(symbol)
        prev_entry = prev_indices.get(key, {})
        indices[key] = {
            "value": round(price, 2) if price is not None else prev_entry.get("value"),
            "changePct": round(change_pct, 2) if change_pct is not None else prev_entry.get("changePct"),
        }
    return indices


def fetch_headlines(limit=5):
    try:
        query = urllib.parse.quote("미국 증시")
        url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        root = ET.fromstring(http_get(url))
        items = []
        for item in root.findall("./channel/item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            if title and link:
                items.append({"tag": "시장 뉴스", "title": title, "source": source, "link": link})
        return items
    except Exception as exc:
        print(f"[warn] fetch_headlines failed: {exc}")
        return []


def main():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    us10y = fred_latest("DGS10", prev.get("us10y"))
    us30y = fred_latest("DGS30", prev.get("us30y"))
    fed_low = fred_latest("DFEDTARL", prev.get("fedFundsLow"))
    fed_high = fred_latest("DFEDTARU", prev.get("fedFundsHigh"))

    usd_krw, _ = yahoo_quote(YAHOO_FX_SYMBOLS["usdKrw"])
    usd_jpy, _ = yahoo_quote(YAHOO_FX_SYMBOLS["usdJpy"])
    # Yahoo's direct JPYKRW=X quote only has 2 decimals, so derive it instead.
    if usd_krw and usd_jpy:
        jpy_krw_100 = usd_krw / usd_jpy * 100
    else:
        jpy_krw_100 = prev.get("jpyKrw100")
    if usd_krw is None:
        usd_krw = prev.get("usdKrw")
    if usd_jpy is None:
        usd_jpy = prev.get("usdJpy")

    indices = fetch_indices(prev.get("indices", {}))

    issues = fetch_headlines() or prev.get("issues", [])

    now_utc = datetime.datetime.utcnow()
    now_kst = now_utc + datetime.timedelta(hours=9)

    result = {
        "updatedAtISO": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updatedAtLabel": f"자동 갱신: {now_kst.strftime('%Y.%m.%d %H:%M')} (한국시간)",
        "us10y": round(us10y, 2) if us10y is not None else None,
        "us30y": round(us30y, 2) if us30y is not None else None,
        "fedFundsLow": fed_low,
        "fedFundsHigh": fed_high,
        "krBase": prev.get("krBase", 3.00),
        "krBaseNote": prev.get(
            "krBaseNote", "한국은행 금융통화위원회 (변경 시 이 파일의 krBase 값을 직접 수정)"
        ),
        "usdKrw": round(usd_krw, 2) if usd_krw is not None else None,
        "usdJpy": round(usd_jpy, 2) if usd_jpy is not None else None,
        "jpyKrw100": round(jpy_krw_100, 2) if jpy_krw_100 is not None else None,
        "indices": indices,
        "issues": issues,
        "sourceNote": (
            "자료: FRED(연준), Yahoo Finance(환율·지수), 구글 뉴스 등 공개 데이터를 "
            "자동으로 모은 참고용 정보이며 실제 거래·투자 판단 전에는 각 기관의 공식 고시를 확인하세요."
        ),
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("data.json updated:")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:600])


if __name__ == "__main__":
    main()
