#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news.py — новостная лента рыночных/макро-событий из GDELT DOC 2.0 (без ключа),
с подсветкой упоминаний отслеживаемых компаний из fundamentals.csv."""
import sys, os, re, csv, json, argparse, datetime as dt
import urllib.parse

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

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERY = ('(stock market OR "Federal Reserve" OR "interest rates" OR inflation '
         'OR earnings OR IPO OR recession OR tariffs) sourcelang:english')

SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "ltd", "limited", "co",
    "company", "holdings", "holding", "group", "plc",
}


def fetch_articles():
    params = {
        "query": QUERY, "mode": "ArtList", "format": "json",
        "maxrecords": "75", "sort": "DateDesc", "timespan": "3d",
    }
    url = f"{GDELT_URL}?{urllib.parse.urlencode(params)}"
    raw = _get(url)
    data = json.loads(raw)
    return data.get("articles", [])


def clean_company_name(name):
    words = re.sub(r"[^a-zA-Z0-9]+", " ", name or "").split()
    kept = [w for w in words if w.lower() not in SUFFIX_WORDS]
    cleaned = " ".join(kept).strip()
    return cleaned if cleaned else (name or "").strip()


def load_tracked(path):
    if not os.path.exists(path):
        print(f"[!] {path} не найден", file=sys.stderr)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    tracked = []
    seen = set()
    for r in rows:
        ticker = (r.get("ticker") or "").strip().upper()
        company = clean_company_name(r.get("company") or "")
        if not ticker and not company:
            continue
        label = ticker or company
        key = (ticker, company.lower())
        if key in seen:
            continue
        seen.add(key)
        tracked.append({"label": label, "ticker": ticker, "company": company})
    return tracked


def find_mentions(title, tracked):
    # оба варианта — по границе слова: substring-проверка на "чистом" имени компании
    # ловит его ВНУТРИ других слов ("TEN" из "TEN Holdings" совпадало в "attend", "often")
    mentions = []
    for entity in tracked:
        matched = False
        if entity["ticker"] and re.search(rf"\b{re.escape(entity['ticker'])}\b", title):
            matched = True
        elif entity["company"] and len(entity["company"]) >= 3 and \
                re.search(rf"\b{re.escape(entity['company'])}\b", title, re.I):
            matched = True
        if matched:
            mentions.append(entity["label"])
    return mentions


def parse_seendate(raw):
    try:
        d = dt.datetime.strptime(raw, "%Y%m%dT%H%M%SZ")
        return d.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return raw or ""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Новости и события</title>
<style>
  :root {
    --border: #e2e2e7; --muted: #6b6b76; --bg: #ffffff; --bg-alt: #f8f8fa; --accent: #2451c7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #16161a; background: var(--bg-alt);
  }
  header { padding: 20px 16px 14px; background: var(--bg); border-bottom: 1px solid var(--border); }
  nav.pages { margin-bottom: 10px; font-size: 13px; }
  nav.pages a { color: var(--muted); text-decoration: none; margin-right: 14px; padding-bottom: 2px; }
  nav.pages a.active { color: var(--accent); font-weight: 600; border-bottom: 2px solid var(--accent); }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
  .disclaimer {
    font-size: 12px; color: var(--muted); background: var(--bg-alt);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; margin-bottom: 12px; max-width: 760px;
  }
  .counts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin-bottom: 14px; }
  .counts b { font-size: 16px; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  #search {
    flex: 1 1 220px; min-width: 160px; padding: 8px 10px;
    border: 1px solid var(--border); border-radius: 6px; font-size: 14px;
  }
  .filter-btn {
    padding: 7px 12px; border: 1px solid var(--border); background: var(--bg);
    border-radius: 6px; cursor: pointer; font-size: 13px;
  }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  main { padding: 12px 16px 40px; }
  .feed { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .item { padding: 12px 14px; border-bottom: 1px solid var(--border); }
  .item:last-child { border-bottom: none; }
  .item:hover { background: var(--bg-alt); }
  .item-date { color: var(--muted); font-size: 12px; margin-bottom: 2px; }
  .item-title a { color: #16161a; font-weight: 600; text-decoration: none; font-size: 14px; }
  .item-title a:hover { text-decoration: underline; color: var(--accent); }
  .item-domain { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .mentions-badge {
    display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; background: var(--accent); color: #fff;
  }
  .empty { text-align: center; color: var(--muted); padding: 30px; }
  @media (max-width: 640px) { h1 { font-size: 19px; } .counts { gap: 10px; } }
</style>
</head>
<body>
<header>
  <nav class="pages">
    <a href="index.html">Обзор</a>
    <a href="startups.html">Стартапы</a>
    <a href="ipo.html">IPO Pipeline</a>
    <a href="fundamentals.html">Фундаментал</a>
    <a href="events.html">События</a>
    <a href="news.html" class="active">Новости</a>
  </nav>
  <h1>Новости и события</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="disclaimer">
    Лента из GDELT (открытый агрегатор мировых новостей, англоязычные источники) — быстрый
    обзор рыночного/макро-фона, сырой поток, не проверенные факты. Отмечены статьи про
    отслеживаемые компании.
  </div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
    <span>Про мои компании: <b>__MENTIONS_COUNT__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по заголовку, источнику...">
    <button class="filter-btn" id="onlyMentionsBtn">Только про мои компании</button>
  </div>
</header>
<main>
  <div class="feed" id="feed"></div>
</main>
<script>
const DATA = __DATA_JSON__;

let searchTerm = "";
let onlyMentions = false;

function render() {
  const feed = document.getElementById("feed");
  feed.innerHTML = "";

  let items = DATA;
  if (onlyMentions) items = items.filter(r => r.mentions && r.mentions.length > 0);
  if (searchTerm) {
    const t = searchTerm.toLowerCase();
    items = items.filter(r =>
      (r.title || "").toLowerCase().includes(t) ||
      (r.domain || "").toLowerCase().includes(t) ||
      (r.mentions || []).some(m => m.toLowerCase().includes(t))
    );
  }

  if (items.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "Ничего не найдено";
    feed.appendChild(div);
    return;
  }

  for (const row of items) {
    const item = document.createElement("div");
    item.className = "item";

    const date = document.createElement("div");
    date.className = "item-date";
    date.textContent = row.seendate || "";
    item.appendChild(date);

    const titleDiv = document.createElement("div");
    titleDiv.className = "item-title";
    const a = document.createElement("a");
    a.href = row.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = row.title;
    titleDiv.appendChild(a);
    item.appendChild(titleDiv);

    const domain = document.createElement("div");
    domain.className = "item-domain";
    domain.textContent = row.domain || "";
    item.appendChild(domain);

    if (row.mentions && row.mentions.length > 0) {
      const badge = document.createElement("div");
      badge.className = "mentions-badge";
      badge.textContent = "↳ про: " + row.mentions.join(", ");
      item.appendChild(badge);
    }

    feed.appendChild(item);
  }
}

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

document.getElementById("onlyMentionsBtn").addEventListener("click", (e) => {
  onlyMentions = !onlyMentions;
  e.target.classList.toggle("active", onlyMentions);
  render();
});

render();
</script>
</body>
</html>
"""


def build_html(rows):
    mentions_count = sum(1 for r in rows if r["mentions"])
    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__GENERATED_AT__", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(rows)))
            .replace("__MENTIONS_COUNT__", str(mentions_count))
            .replace("__DATA_JSON__", data_json))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fundamentals-csv", default="fundamentals.csv")
    ap.add_argument("--out", default="news.csv")
    ap.add_argument("--html-out", default="public/news.html")
    a = ap.parse_args()

    tracked = load_tracked(a.fundamentals_csv)
    print(f"[i] Отслеживаемых компаний: {len(tracked)}", file=sys.stderr)

    try:
        articles = fetch_articles()
    except Exception as ex:
        print(f"[!] Ошибка GDELT: {ex}", file=sys.stderr)
        articles = []

    print(f"[i] Статей получено: {len(articles)}", file=sys.stderr)

    rows = []
    for art in articles:
        title = art.get("title", "")
        mentions = find_mentions(title, tracked)
        rows.append({
            "seendate": parse_seendate(art.get("seendate", "")),
            "title": title,
            "domain": art.get("domain", ""),
            "url": art.get("url", ""),
            "mentions": mentions,
        })

    rows.sort(key=lambda r: r["seendate"], reverse=True)
    with_mentions = sum(1 for r in rows if r["mentions"])
    print(f"[i] С упоминанием отслеживаемых: {with_mentions}", file=sys.stderr)

    cols = ["seendate", "title", "domain", "url", "mentions"]
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "mentions": ", ".join(r["mentions"])})
    print(f"[OK] Сохранено {len(rows)} строк -> {a.out}", file=sys.stderr)

    os.makedirs(os.path.dirname(a.html_out), exist_ok=True)
    with open(a.html_out, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print(f"[OK] -> {a.html_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
