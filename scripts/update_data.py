"""
Fetches fresh market data from free, no-key public sources and writes it into
data.json. This script is run once a day by the GitHub Actions workflow at
.github/workflows/update-data.yml — you should not normally need to run it
yourself, but you can (`python scripts/update_data.py`) to test changes.

Sources used (all free, no API key/account required):
  - FRED (Federal Reserve Bank of St. Louis) CSV export: 10yr/30yr Treasury
    yields and the Fed funds target range.
  - Frankfurter.app: USD/KRW, USD/JPY, JPY/KRW exchange rates (based on ECB
    reference rates).
  - Google News RSS: top Korean-language "미국 증시" headlines.

Korea's base rate (krBase) is NOT auto-fetched, because there is no reliable
no-key public API for it and it only changes a handful of times a year at
scheduled Bank of Korea meetings. Update it by hand in data.json (the
"krBase" number) whenever the BOK announces a change.
"""

import json
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DATA_FILE = "data.json"


def http_get(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; MorningMarketBrief/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fred_latest(series_id, default=None):
    """Returns the most recent non-missing value for a FRED series, using the
    public, no-key CSV export endpoint."""
    try:
        csv_text = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}")
        lines = [l.strip() for l in csv_text.strip().splitlines() if l.strip()]
        for line in reversed(lines[1:]):  # skip header row, walk backwards from today
            parts = line.split(",")
            if len(parts) < 2:
                continue
            value = parts[-1].strip()
            if value in ("", "."):
                continue  # FRED marks non-trading days with "."
            return float(value)
    except Exception as exc:
        print(f"[warn] fred_latest({series_id}) failed: {exc}")
    return default


def frankfurter_rates(base, symbols):
    try:
        payload = json.loads(http_get(f"https://api.frankfurter.app/latest?from={base}&to={symbols}"))
        return payload.get("rates", {})
    except Exception as exc:
        print(f"[warn] frankfurter({base}->{symbols}) failed: {exc}")
        return {}


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

    usd_rates = frankfurter_rates("USD", "KRW,JPY")
    usd_krw = usd_rates.get("KRW", prev.get("usdKrw"))
    usd_jpy = usd_rates.get("JPY", prev.get("usdJpy"))

    jpy_rates = frankfurter_rates("JPY", "KRW")
    jpy_krw_raw = jpy_rates.get("KRW")
    jpy_krw_100 = jpy_krw_raw * 100 if jpy_krw_raw else prev.get("jpyKrw100")

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
        "fedNote": prev.get("fedNote", "FOMC 정책금리 목표범위"),
        "krBase": prev.get("krBase", 3.00),
        "krBaseNote": prev.get(
            "krBaseNote", "한국은행 금융통화위원회 (변경 시 이 파일의 krBase 값을 직접 수정)"
        ),
        "usdKrw": round(usd_krw, 2) if usd_krw is not None else None,
        "usdJpy": round(usd_jpy, 2) if usd_jpy is not None else None,
        "jpyKrw100": round(jpy_krw_100, 2) if jpy_krw_100 is not None else None,
        "issues": issues,
        "sourceNote": (
            "자료: FRED(연준), Frankfurter(환율), 구글 뉴스 등 공개 데이터를 자동으로 모은 "
            "참고용 정보이며 실제 거래·투자 판단 전에는 각 기관의 공식 고시를 확인하세요."
        ),
    }

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("data.json updated:")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:600])


if __name__ == "__main__":
    main()
