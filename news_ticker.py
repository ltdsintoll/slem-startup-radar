#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news_ticker.py — новости, привязанные к конкретной бумаге (тикеру/компании), с NLP-тональностью.

Отличие от news.py: тот тянет ОДНУ общую макро-ленту без привязки к тикеру.
Этот скрипт делает точечный запрос ПО КАЖДОЙ компании из fundamentals.csv
(watchlist + свежие Priced/IPO) и даёт precision-first фильтрацию релевантности
(лучше меньше новостей, но точно про эту компанию, чем шум).

Источники по приоритету:
  1) GDELT DOC 2.0 (как в news.py) — бесплатно, без ключа, но агрессивно
     рейт-лимитит (429); обрабатывается мягко — retry с бэкоффом, затем
     тихий пропуск на этот тикер.
  2) NewsData.io free tier — нужен API-ключ (NEWSDATA_API_KEY в окружении).
     Без ключа источник тихо пропускается — н/д, а не выдумка.
"""
import sys, os, re, csv, json, time, argparse, datetime as dt
import urllib.parse

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
try:
    import requests
    def _get(url, timeout=20):
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        return r.status_code, r.content
except Exception:
    import urllib.request, urllib.error
    def _get(url, timeout=20):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

FUNDAMENTALS_CSV = "fundamentals.csv"
OUT_JSON = "company_news.json"

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MAX_RETRIES = 2
GDELT_RETRY_WAIT = 5  # сек, "one request per ~5 seconds" по их же 429-сообщению
GDELT_PAUSE_BETWEEN_COMPANIES = 6  # сек

NEWSDATA_URL = "https://newsdata.io/api/1/news"
NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY", "")

MAX_ARTICLES_PER_COMPANY = 5

SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "ltd", "limited", "co",
    "company", "holdings", "holding", "group", "plc",
}

# простой прозрачный лексикон финансовой тональности — никакого чёрного ящика:
# итоговый скор = (число позитивных слов) - (число негативных слов) в заголовке
POSITIVE_WORDS = {
    "surge", "surges", "surged", "soar", "soars", "soared", "jump", "jumps", "jumped",
    "rally", "rallies", "rallied", "beat", "beats", "record", "growth", "profit",
    "profits", "profitable", "upgrade", "upgrades", "upgraded", "gain", "gains",
    "strong", "outperform", "outperforms", "breakthrough", "win", "wins", "winning",
    "success", "successful", "expand", "expands", "expansion", "raise", "raises",
    "raised", "approval", "approved", "approves", "partnership", "launch", "launches",
    "launched", "boost", "boosts", "boosted", "recovery", "rebound", "rebounds",
    "bullish", "upbeat", "soaring", "milestone", "innovative", "leader", "leading",
}
NEGATIVE_WORDS = {
    "plunge", "plunges", "plunged", "crash", "crashes", "crashed", "drop", "drops",
    "dropped", "fall", "falls", "falling", "fell", "miss", "misses", "missed",
    "downgrade", "downgrades", "downgraded", "loss", "losses", "lawsuit", "fraud",
    "investigation", "probe", "recall", "recalls", "layoff", "layoffs", "bankrupt",
    "bankruptcy", "default", "decline", "declines", "declined", "slump", "warning",
    "warns", "cut", "cuts", "delay", "delayed", "fail", "fails", "failure", "weak",
    "concern", "concerns", "risk", "risks", "selloff", "sued", "suit", "scandal",
    "bearish", "downturn", "struggling", "trouble", "troubled", "halt", "halted",
}


def clean_company_name(name):
    words = re.sub(r"[^a-zA-Z0-9]+", " ", name or "").split()
    kept = [w for w in words if w.lower() not in SUFFIX_WORDS]
    cleaned = " ".join(kept).strip()
    return cleaned if cleaned else (name or "").strip()


def score_sentiment(title):
    words = re.findall(r"[a-zA-Z']+", title.lower())
    pos = sorted(set(w for w in words if w in POSITIVE_WORDS))
    neg = sorted(set(w for w in words if w in NEGATIVE_WORDS))
    raw = len(pos) - len(neg)
    if raw > 0:
        label = "позитив"
    elif raw < 0:
        label = "негатив"
    else:
        label = "нейтрально"
    return {"label": label, "score": raw, "matched_positive": pos, "matched_negative": neg}


def is_relevant(title, ticker, company_clean):
    """Precision-first: тикер как отдельное слово (регистрозависимо) ИЛИ полное чищеное
    имя компании как словосочетание. Одно короткое слово компании не считается —
    тот же урок, что и в news.py (иначе "TEN" ловит "attend")."""
    if ticker and re.search(rf"\b{re.escape(ticker)}\b", title):
        return True
    if company_clean and len(company_clean) >= 4 and re.search(rf"\b{re.escape(company_clean)}\b", title, re.I):
        return True
    return False


def parse_gdelt_seendate(raw):
    try:
        return dt.datetime.strptime(raw, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return raw or ""


def fetch_gdelt(query, days):
    params = {
        "query": query, "mode": "ArtList", "format": "json",
        "maxrecords": "20", "sort": "DateDesc", "timespan": f"{days}d",
    }
    url = f"{GDELT_URL}?{urllib.parse.urlencode(params)}"

    for attempt in range(GDELT_MAX_RETRIES + 1):
        status, raw = _get(url)
        if status == 200:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return None, "parse-error"
            return data.get("articles", []), "ok"
        if status == 429:
            if attempt < GDELT_MAX_RETRIES:
                time.sleep(GDELT_RETRY_WAIT)
                continue
            return None, "rate-limited"
        return None, f"http-{status}"
    return None, "rate-limited"


def fetch_newsdata(query, days):
    if not NEWSDATA_API_KEY:
        return None, "no-key"
    params = {"apikey": NEWSDATA_API_KEY, "q": query, "language": "en"}
    url = f"{NEWSDATA_URL}?{urllib.parse.urlencode(params)}"
    status, raw = _get(url)
    if status != 200:
        return None, f"http-{status}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "parse-error"
    if data.get("status") != "success":
        return None, "api-error"

    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    articles = []
    for r in data.get("results", []) or []:
        pub_date = (r.get("pubDate") or "")[:10]
        if pub_date and pub_date < cutoff:
            continue
        articles.append({
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "domain": r.get("source_id", ""),
            "seendate": pub_date,
        })
    return articles, "ok"


def gdelt_query_for(ticker, company_clean):
    parts = []
    if company_clean:
        parts.append(f'"{company_clean}"')
    if ticker:
        parts.append(ticker)
    inner = " OR ".join(parts) if parts else company_clean
    return f"({inner}) sourcelang:english"


def news_for_company(ticker, company, days):
    company_clean = clean_company_name(company)
    query = gdelt_query_for(ticker, company_clean)

    articles_raw, gdelt_status = fetch_gdelt(query, days)
    source_used = "gdelt"
    if not articles_raw:
        print(f"    GDELT: {gdelt_status}", file=sys.stderr)
        articles_raw, nd_status = fetch_newsdata(query, days)
        source_used = "newsdata"
        if not articles_raw:
            print(f"    NewsData: {nd_status}", file=sys.stderr)
            return [], "no-data"

    seen = set()
    result = []
    for art in articles_raw:
        title = art.get("title", "")
        url = art.get("url", "")
        if not title or not url:
            continue
        # дедуп по нормализованному заголовку, не по URL: GDELT иногда отдаёт одну и
        # ту же статью с вариантами URL (http/https, порт :443 и т.п.)
        title_key = re.sub(r"\s+", " ", title.strip().lower())
        if title_key in seen:
            continue
        if not is_relevant(title, ticker, company_clean):
            continue
        seen.add(title_key)
        sentiment = score_sentiment(title)
        result.append({
            "title": title,
            "url": url,
            "domain": art.get("domain", ""),
            "date": art.get("seendate", "") if source_used == "newsdata" else parse_gdelt_seendate(art.get("seendate", "")),
            "sentiment_label": sentiment["label"],
            "sentiment_score": sentiment["score"],
        })
        if len(result) >= MAX_ARTICLES_PER_COMPANY:
            break

    if not result:
        return [], "no-relevant-matches"
    return result, source_used


def load_universe(path, watchlist_only=False):
    if not os.path.exists(path):
        print(f"[!] {path} не найден", file=sys.stderr)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        cik = (r.get("cik") or "").strip()
        if not cik:
            continue
        if watchlist_only and "Watchlist" not in (r.get("universe") or ""):
            continue
        out.append({"cik": cik, "ticker": (r.get("ticker") or "").strip(), "company": r.get("company", "")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0, help="обработать только первые N компаний (0 = все)")
    ap.add_argument("--watchlist-only", action="store_true",
                     help="только тикеры из watchlist.txt, без свежих Priced/IPO — для нечастого CI-шага")
    ap.add_argument("--fundamentals-csv", default=FUNDAMENTALS_CSV)
    ap.add_argument("--out", default=OUT_JSON)
    a = ap.parse_args()

    universe = load_universe(a.fundamentals_csv, watchlist_only=a.watchlist_only)
    if a.limit:
        universe = universe[:a.limit]
    print(f"[i] Компаний в обработке: {len(universe)}", file=sys.stderr)
    if not NEWSDATA_API_KEY:
        print("[i] NEWSDATA_API_KEY не задан — второй источник (NewsData.io) будет пропускаться", file=sys.stderr)

    out = {}
    stats = {"gdelt": 0, "newsdata": 0, "no-data": 0}
    for i, entry in enumerate(universe, 1):
        label = entry["ticker"] or entry["company"]
        print(f"[{i}/{len(universe)}] {label}", file=sys.stderr)
        articles, source = news_for_company(entry["ticker"], entry["company"], a.days)
        out[entry["cik"]] = {
            "ticker": entry["ticker"],
            "company": entry["company"],
            "articles": articles,
            "source": source if articles else "no-data",
        }
        if articles:
            stats[source] = stats.get(source, 0) + 1
            print(f"    -> {len(articles)} релевантных статей ({source})", file=sys.stderr)
        else:
            stats["no-data"] += 1
            print(f"    -> н/д ({source})", file=sys.stderr)
        if i < len(universe):
            time.sleep(GDELT_PAUSE_BETWEEN_COMPANIES)

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[i] Итог: gdelt={stats.get('gdelt',0)} newsdata={stats.get('newsdata',0)} "
          f"н/д={stats.get('no-data',0)}", file=sys.stderr)
    print(f"[OK] -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
