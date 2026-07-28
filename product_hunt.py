#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""product_hunt.py — свежие запуски продуктов с Product Hunt (GraphQL API v2).

Третий источник для startups.html (после Form D и YC). Нужен бесплатный
Developer Token с api.producthunt.com (переменная PH_API_TOKEN) — без него
шаг тихо пропускается, пайплайн не падает (тот же паттерн, что
FRED_API_KEY в macro.py / NEWSDATA_API_KEY в news_ticker.py).
"""
import sys, os, csv, json, time, argparse, datetime as dt
import urllib.request

PH_API_TOKEN = os.environ.get("PH_API_TOKEN", "")
PH_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
OUT_CSV = "product_hunt.csv"
PAGE_SIZE = 50

QUERY = """
query RecentPosts($postedAfter: DateTime, $after: String) {
  posts(postedAfter: $postedAfter, order: NEWEST, first: %d, after: $after) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        commentsCount
        createdAt
        topics(first: 5) { edges { node { name } } }
        makers { name }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % PAGE_SIZE


def _post_graphql(variables):
    body = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        PH_GRAPHQL_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {PH_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_posts(days, max_results):
    posted_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts, after = [], None
    while len(posts) < max_results:
        try:
            data = _post_graphql({"postedAfter": posted_after, "after": after})
        except Exception as ex:
            print(f"[!] Ошибка запроса к Product Hunt: {ex}", file=sys.stderr)
            break
        if data.get("errors"):
            print(f"[!] Product Hunt GraphQL errors: {data['errors']}", file=sys.stderr)
            break
        conn = (data.get("data") or {}).get("posts") or {}
        edges = conn.get("edges") or []
        if not edges:
            break
        for e in edges:
            node = e.get("node") or {}
            posts.append(node)
            if len(posts) >= max_results:
                break
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or len(posts) >= max_results:
            break
        after = page_info.get("endCursor")
        time.sleep(0.3)
    return posts


def flatten_topics(node):
    edges = ((node.get("topics") or {}).get("edges")) or []
    return ", ".join(e["node"]["name"] for e in edges if e.get("node", {}).get("name"))


def flatten_makers(node):
    makers = node.get("makers") or []
    return ", ".join(m["name"] for m in makers if m.get("name"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max", type=int, default=50)
    ap.add_argument("--out", default=OUT_CSV)
    a = ap.parse_args()

    if not PH_API_TOKEN:
        print("[i] PH_API_TOKEN не задан — Product Hunt пропускается", file=sys.stderr)
        return

    print(f"[i] Product Hunt: запуски за {a.days} дн. (до {a.max})", file=sys.stderr)
    posts = fetch_posts(a.days, a.max)
    if not posts:
        print("[i] Product Hunt: 0 запусков — файл не пишу", file=sys.stderr)
        return

    rows = []
    for p in posts:
        rows.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "tagline": p.get("tagline", ""),
            "description": p.get("description", "") or p.get("tagline", ""),
            "url": p.get("url", ""),
            "website": p.get("website", "") or "",
            "votes": p.get("votesCount", ""),
            "comments": p.get("commentsCount", ""),
            "topics": flatten_topics(p),
            "makers": flatten_makers(p),
            "created_at": (p.get("createdAt", "") or "")[:10],
        })
        print(f"  {rows[-1]['name'][:40]:40} | 👍{rows[-1]['votes']:>5} | 💬{rows[-1]['comments']:>4} | {rows[-1]['created_at']}",
              file=sys.stderr)

    cols = ["id", "name", "tagline", "description", "url", "website", "votes", "comments",
            "topics", "makers", "created_at"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[OK] Сохранено {len(rows)} строк -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
