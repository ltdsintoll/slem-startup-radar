#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""companies_house.py — свежие регистрации компаний UK через официальный
Companies House Advanced Search API (api.company-information.service.gov.uk).

Четвёртый источник для startups.html. Нужен бесплатный ключ разработчика
(переменная COMPANIES_HOUSE_API_KEY) — без него шаг тихо пропускается,
пайплайн не падает (тот же паттерн, что PH_API_TOKEN/FRED_API_KEY/
NEWSDATA_API_KEY).

Companies House регистрирует ВСЕ юрлица UK, не только стартапы, поэтому
фильтруем на уровне самого API-запроса:
  - company_status=active — не показываем dissolved/liquidation и т.п.
  - company_type=ltd — приватная компания с ограниченной ответственностью,
    структурный (не по имени) аналог is_vehicle_name() из form_d.py: LLP,
    limited-partnership, royal-charter, industrial-and-provident-society
    и т.п. в выдачу вообще не попадают, т.к. Companies House отдаёт тип
    юрлица официальным полем, а не самоотчётом в свободном тексте, как SEC
    industryGroupType — гадать по имени не нужно.
  - SIC_WHITELIST — аналог STARTUP_INDUSTRIES: только SIC-коды 2007,
    типичные для tech/software/biotech компаний.
Внутри "ltd" всё ещё возможны holding/SPV-компании (например "XYZ Ventures
Ltd") — по имени НЕ фильтруем: нет реальных данных, чтобы откалибровать
паттерн без ложных срабатываний (тот же принцип, что и в прошлый раз
с Product Hunt) — явный follow-up, когда появятся реальные данные.
"""
import sys, os, csv, time, json, argparse, datetime as dt
import base64
import urllib.request, urllib.parse

COMPANIES_HOUSE_API_KEY = os.environ.get("COMPANIES_HOUSE_API_KEY", "")
SEARCH_URL = "https://api.company-information.service.gov.uk/advanced-search/companies"
OUT_CSV = "companies_house.csv"
PAGE_SIZE = 100

# SIC 2007 — коды, типичные для tech/software/biotech стартапов. Не исчерпывающе,
# как STARTUP_INDUSTRIES для Form D — уточнить набор, когда будут реальные данные.
SIC_WHITELIST = {
    "58290": "Other software publishing",
    "62011": "Ready-made interactive leisure and entertainment software development",
    "62012": "Business and domestic software development",
    "62020": "Information technology consultancy activities",
    "62030": "Computer facilities management activities",
    "62090": "Other information technology and computer service activities",
    "63110": "Data processing, hosting and related activities",
    "63120": "Web portals",
    "63990": "Other information service activities n.e.c.",
    "72110": "Research and experimental development on biotechnology",
    "72190": "Other research and experimental development on natural sciences and engineering",
}


def _get(url):
    token = base64.b64encode(f"{COMPANIES_HOUSE_API_KEY}:".encode()).decode()
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_companies(days, max_results):
    incorporated_from = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    sic_codes = ",".join(sorted(SIC_WHITELIST))
    items, start_index = [], 0

    while len(items) < max_results:
        params = {
            "incorporated_from": incorporated_from,
            "company_status": "active",
            "company_type": "ltd",
            "sic_codes": sic_codes,
            "size": str(min(PAGE_SIZE, max_results - len(items))),
            "start_index": str(start_index),
        }
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        try:
            data = _get(url)
        except Exception as ex:
            print(f"[!] Ошибка запроса к Companies House: {ex}", file=sys.stderr)
            break
        page = data.get("items", []) or []
        if not page:
            break
        items.extend(page)
        total_hits = data.get("hits", 0)
        start_index += len(page)
        if start_index >= total_hits or len(page) < PAGE_SIZE:
            break
        time.sleep(0.3)
    return items[:max_results]


def sic_labels(codes):
    return ", ".join(SIC_WHITELIST.get(c, c) for c in (codes or []) if c in SIC_WHITELIST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--out", default=OUT_CSV)
    a = ap.parse_args()

    if not COMPANIES_HOUSE_API_KEY:
        print("[i] COMPANIES_HOUSE_API_KEY не задан — Companies House пропускается", file=sys.stderr)
        return

    print(f"[i] Companies House: регистрации за {a.days} дн. (до {a.max}, "
          f"SIC-фильтр: {len(SIC_WHITELIST)} кодов)", file=sys.stderr)
    items = fetch_companies(a.days, a.max)
    if not items:
        print("[i] Companies House: 0 регистраций — файл не пишу", file=sys.stderr)
        return

    rows = []
    for it in items:
        addr = it.get("registered_office_address", {}) or {}
        codes = it.get("sic_codes", []) or []
        rows.append({
            "company_number": it.get("company_number", ""),
            "company_name": it.get("company_name", ""),
            "company_status": it.get("company_status", ""),
            "company_type": it.get("company_type", ""),
            "date_of_creation": it.get("date_of_creation", ""),
            "sic_codes": ";".join(codes),
            "sic_labels": sic_labels(codes),
            "locality": addr.get("locality", "") or "",
            "region": addr.get("region", "") or "",
            "country": addr.get("country", "") or "",
        })
        print(f"  {rows[-1]['company_name'][:40]:40} | {rows[-1]['date_of_creation']} | "
              f"{rows[-1]['sic_labels'][:40]}", file=sys.stderr)

    cols = ["company_number", "company_name", "company_status", "company_type", "date_of_creation",
            "sic_codes", "sic_labels", "locality", "region", "country"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[OK] Сохранено {len(rows)} строк -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
