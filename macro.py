#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macro.py — снимок кривой доходности US Treasury (3M/2Y/10Y, спред 2s10s) плюс,
если задан FRED_API_KEY, ставка ФРС (FEDFUNDS) и инфляция (CPIAUCSL, год к году) —
для баннера на обзоре."""
import sys, os, json, datetime as dt
import urllib.parse
from xml.etree import ElementTree as ET

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
try:
    import requests
    def _get(url):
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        return r.content
except Exception:
    import urllib.request
    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

XML_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
OUT_JSON = "macro.json"

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def fetch_month(yyyymm):
    url = f"{XML_URL}?data=daily_treasury_yield_curve&field_tdr_date_value_month={yyyymm}"
    raw = _get(url)
    root = ET.fromstring(raw)
    entries = []
    for entry in root.iter():
        if local(entry.tag) != "entry":
            continue
        props = {}
        for el in entry.iter():
            name = local(el.tag)
            if name in ("NEW_DATE", "BC_3MONTH", "BC_2YEAR", "BC_10YEAR") and el.text:
                props[name] = el.text.strip()
        if props.get("NEW_DATE"):
            entries.append(props)
    return entries


def latest_record(entries):
    def date_key(e):
        return e.get("NEW_DATE", "")
    return max(entries, key=date_key)


def fetch_treasury_macro():
    today = dt.date.today()
    months_to_try = [today.strftime("%Y%m")]
    prev_month = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y%m")
    months_to_try.append(prev_month)

    entries = []
    for yyyymm in months_to_try:
        try:
            entries = fetch_month(yyyymm)
        except Exception as ex:
            print(f"[!] Ошибка Treasury XML ({yyyymm}): {ex}", file=sys.stderr)
            entries = []
        if entries:
            print(f"[i] {yyyymm}: {len(entries)} записей", file=sys.stderr)
            break
        print(f"[i] {yyyymm}: пусто, пробую предыдущий месяц", file=sys.stderr)

    if not entries:
        print("[!] Нет данных Treasury ни за текущий, ни за предыдущий месяц", file=sys.stderr)
        return {}

    rec = latest_record(entries)
    try:
        date_str = rec["NEW_DATE"].split("T")[0]
        y3m = float(rec["BC_3MONTH"])
        y2 = float(rec["BC_2YEAR"])
        y10 = float(rec["BC_10YEAR"])
    except (KeyError, ValueError) as ex:
        print(f"[!] Неполная запись Treasury, пропускаю: {ex}", file=sys.stderr)
        return {}

    spread = round(y10 - y2, 2)
    print(f"[OK] Treasury {date_str}: 3M={y3m} 2Y={y2} 10Y={y10} spread_2s10s={spread}", file=sys.stderr)
    return {"date": date_str, "y3m": y3m, "y2": y2, "y10": y10, "spread_2s10s": spread}


def fred_observations(series_id, limit):
    params = {
        "series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
        "sort_order": "desc", "limit": str(limit),
    }
    url = f"{FRED_URL}?{urllib.parse.urlencode(params)}"
    raw = _get(url)
    data = json.loads(raw)
    return [o for o in data.get("observations", []) if o.get("value") not in (None, ".")]


def fetch_fred_macro():
    """Ставка ФРС (FEDFUNDS) и инфляция CPI год-к-году (CPIAUCSL), с дельтой к
    предыдущему релизу — обе дельты считаются из одного запроса с историей
    наблюдений, без отдельного файла состояния."""
    if not FRED_API_KEY:
        print("[i] FRED_API_KEY не задан — ставка/инфляция пропускаются", file=sys.stderr)
        return {}

    out = {}

    try:
        obs = fred_observations("FEDFUNDS", 2)
        if len(obs) >= 2:
            rate_now, rate_prev = float(obs[0]["value"]), float(obs[1]["value"])
            out["fed_funds_rate"] = rate_now
            out["fed_funds_date"] = obs[0]["date"]
            out["fed_funds_delta"] = round(rate_now - rate_prev, 2)
            print(f"[OK] FRED FEDFUNDS {obs[0]['date']}: {rate_now} (Δ {out['fed_funds_delta']:+.2f})",
                  file=sys.stderr)
    except Exception as ex:
        print(f"[!] FRED FEDFUNDS: ошибка ({ex})", file=sys.stderr)

    try:
        obs = fred_observations("CPIAUCSL", 15)
        if len(obs) >= 14:
            v = [float(o["value"]) for o in obs]
            yoy_now = (v[0] / v[12] - 1) * 100
            yoy_prev = (v[1] / v[13] - 1) * 100
            out["cpi_yoy"] = round(yoy_now, 2)
            out["cpi_yoy_date"] = obs[0]["date"]
            out["cpi_yoy_delta"] = round(yoy_now - yoy_prev, 2)
            print(f"[OK] FRED CPIAUCSL {obs[0]['date']}: YoY={out['cpi_yoy']} (Δ {out['cpi_yoy_delta']:+.2f})",
                  file=sys.stderr)
    except Exception as ex:
        print(f"[!] FRED CPIAUCSL: ошибка ({ex})", file=sys.stderr)

    return out


def main():
    macro = fetch_treasury_macro()
    macro.update(fetch_fred_macro())

    if not macro:
        print("[!] Ни один источник не дал данных, macro.json не пишу", file=sys.stderr)
        return

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(macro, f, ensure_ascii=False, indent=2)

    print(f"[OK] -> {OUT_JSON}", file=sys.stderr)


if __name__ == "__main__":
    main()
