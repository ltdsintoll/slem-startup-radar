#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yc.py — список стартапов Y Combinator из открытого API yc-oss/api."""
import sys, csv, json, re, argparse
import urllib.request

USER_AGENT = "Slem Invest Research (ltdsintoll@gmail.com)"
ALL_COMPANIES_URL = "https://yc-oss.github.io/api/companies/all.json"

SEASON_RANK = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}
BATCH_RE = re.compile(r"^(Winter|Spring|Summer|Fall)\s+(\d{4})$", re.IGNORECASE)
MIN_BATCH_SIZE = 10  # игнорировать батчи-анонсы с горсткой компаний при выборе --recent


def fetch_companies(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def batch_key(batch):
    m = BATCH_RE.match(batch or "")
    if not m:
        return None
    season, year = m.group(1).lower(), int(m.group(2))
    return (year, SEASON_RANK[season])


def recent_batches(companies, n):
    counts = {}
    names = {}
    for c in companies:
        batch = c.get("batch", "")
        k = batch_key(batch)
        if k is not None:
            counts[k] = counts.get(k, 0) + 1
            names[k] = batch
    keyed = {k: v for k, v in names.items() if counts[k] >= MIN_BATCH_SIZE}
    top_keys = sorted(keyed.keys(), reverse=True)[:n]
    return {keyed[k] for k in top_keys}


def main():
    ap = argparse.ArgumentParser(description="Fetch Y Combinator companies from yc-oss/api")
    ap.add_argument("--batch", default=None, help="фильтр по батчу, частичное совпадение (напр. 'Summer 2025')")
    ap.add_argument("--recent", type=int, default=None, help="взять N последних батчей, если --batch не задан")
    ap.add_argument("--industry", default=None, help="фильтр по отрасли, частичное совпадение")
    ap.add_argument("--active-only", action="store_true")
    ap.add_argument("--out", default="yc.csv")
    args = ap.parse_args()

    print(f"[i] Загружаю {ALL_COMPANIES_URL}", file=sys.stderr)
    companies = fetch_companies(ALL_COMPANIES_URL)
    print(f"[i] Всего компаний: {len(companies)}", file=sys.stderr)

    if args.batch:
        needle = args.batch.lower()
        companies = [c for c in companies if needle in (c.get("batch") or "").lower()]
        print(f"[i] После фильтра --batch '{args.batch}': {len(companies)}", file=sys.stderr)
    else:
        n = args.recent if args.recent is not None else 2
        wanted = recent_batches(companies, n)
        print(f"[i] Последние {n} батчей: {', '.join(sorted(wanted, key=lambda b: batch_key(b), reverse=True))}", file=sys.stderr)
        companies = [c for c in companies if (c.get("batch") or "") in wanted]
        print(f"[i] После фильтра --recent {n}: {len(companies)}", file=sys.stderr)

    if args.industry:
        needle = args.industry.lower()
        companies = [c for c in companies if needle in (c.get("industry") or "").lower()]
        print(f"[i] После фильтра --industry '{args.industry}': {len(companies)}", file=sys.stderr)

    if args.active_only:
        companies = [c for c in companies if (c.get("status") or "") == "Active"]
        print(f"[i] После фильтра --active-only: {len(companies)}", file=sys.stderr)

    rows = []
    for c in companies:
        rows.append({
            "batch": c.get("batch", ""),
            "name": c.get("name", ""),
            "one_liner": c.get("one_liner") or c.get("long_description", ""),
            "industry": c.get("industry", ""),
            "team_size": c.get("team_size", ""),
            "status": c.get("status", ""),
            "website": c.get("website", ""),
            "location": c.get("all_locations", ""),
            "yc_url": c.get("url", ""),
        })

    cols = ["batch", "name", "one_liner", "industry", "team_size", "status", "website", "location", "yc_url"]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[OK] Сохранено {len(rows)} строк -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
