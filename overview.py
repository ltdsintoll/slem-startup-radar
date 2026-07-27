#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""overview.py — главная страница Invest Radar: сводка всех 5 модулей одним взглядом."""
import csv, html, json, os, sys
from datetime import datetime

STARTUPS_JSON = "public/startups_data.json"
NEW_TODAY_JSON = "public/new_today.json"
IPO_NEW_TODAY_JSON = "public/ipo_new_today.json"
IPO_CSV = "ipo.csv"
FUNDAMENTALS_CSV = "fundamentals.csv"
EVENTS_CSV = "events.csv"
NEWS_CSV = "news.csv"
FORMC_CSV = "formc.csv"
MACRO_JSON = "macro.json"
OUT_HTML = "public/index.html"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def load_csv(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


def fmt_usd(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(round(n)):,}"


def fmt_price(v):
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def fund_score_num(r):
    try:
        return int(r.get("score"))
    except (TypeError, ValueError):
        return None


def kpi_tile(title, big, sub, href):
    return f'''<a class="kpi-tile" href="{esc(href)}">
      <div class="kpi-title">{esc(title)}</div>
      <div class="kpi-big">{esc(big)}</div>
      <div class="kpi-sub">{esc(sub)}</div>
    </a>'''


def top_block(title, href, items_html):
    body = "".join(items_html) if items_html else '<div class="top-empty">Нет данных</div>'
    return f'''<div class="top-block">
      <div class="top-header"><h2>{esc(title)}</h2><a href="{esc(href)}">Все →</a></div>
      <div class="top-list">{body}</div>
    </div>'''


def top_item(main, sub):
    return f'''<div class="top-item">
      <div class="top-item-main">{main}</div>
      <div class="top-item-sub">{sub}</div>
    </div>'''


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invest Radar</title>
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
  main { padding: 16px; max-width: 1100px; margin: 0 auto; }
  .macro-banner {
    font-size: 13px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 14px; margin-bottom: 16px;
  }
  .macro-banner .inversion { color: #dc2626; font-weight: 700; }
  .macro-tiles { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
  .macro-tile {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 14px; min-width: 150px; flex: 1 1 150px;
  }
  .macro-tile .t-title { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .macro-tile .t-val { font-size: 20px; font-weight: 700; }
  .macro-tile .t-delta { font-size: 11px; margin-top: 2px; color: var(--muted); }
  .macro-tile .t-delta.rise { color: #dc2626; }
  .macro-tile .t-delta.fall { color: #059669; }
  .macro-attribution { font-size: 11px; color: var(--muted); margin: 4px 0 16px; }
  .macro-attribution a { color: var(--muted); }
  .kpi-row {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px; margin-bottom: 24px;
  }
  .kpi-tile {
    display: block; background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; text-decoration: none; color: inherit;
  }
  .kpi-tile:hover { border-color: var(--accent); }
  .kpi-title { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .kpi-big { font-size: 24px; font-weight: 700; }
  .kpi-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .tops-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
  }
  .top-block { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .top-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; border-bottom: 1px solid var(--border); background: var(--bg-alt);
  }
  .top-header h2 { margin: 0; font-size: 14px; }
  .top-header a { font-size: 12px; color: var(--accent); text-decoration: none; }
  .top-header a:hover { text-decoration: underline; }
  .top-item { padding: 9px 14px; border-bottom: 1px solid var(--border); font-size: 13px; }
  .top-item:last-child { border-bottom: none; }
  .top-item-main { font-weight: 600; }
  .top-item-main a { color: inherit; text-decoration: none; }
  .top-item-main a:hover { color: var(--accent); text-decoration: underline; }
  .top-item-sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .tag {
    display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 10px;
    font-weight: 600; color: #fff; background: var(--muted); margin-left: 6px;
  }
  .mention-badge {
    display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 8px;
    font-size: 10px; font-weight: 600; background: var(--accent); color: #fff;
  }
  .top-empty { padding: 14px; color: var(--muted); font-size: 13px; }
  @media (max-width: 640px) { h1 { font-size: 19px; } }
</style>
</head>
<body>
<header>
  <nav class="pages">
    <a href="index.html" class="active">Обзор</a>
    <a href="startups.html">Стартапы</a>
    <a href="ipo.html">IPO Pipeline</a>
    <a href="fundamentals.html">Фундаментал</a>
    <a href="events.html">События</a>
    <a href="news.html">Новости</a>
    <a href="crowdfunding.html">Краудфандинг</a>
  </nav>
  <h1>Invest Radar</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
</header>
<main>
__MACRO_BANNER__
__FRED_BANNER__
  <div class="kpi-row">
__KPI_TILES__
  </div>
  <div class="tops-grid">
__TOP_BLOCKS__
  </div>
</main>
</body>
</html>
"""


def build_macro_banner():
    macro = load_json(MACRO_JSON, None)
    if not macro:
        return ""
    try:
        y3m, y2, y10 = float(macro["y3m"]), float(macro["y2"]), float(macro["y10"])
        spread = float(macro["spread_2s10s"])
        date_str = macro["date"]
    except (KeyError, TypeError, ValueError):
        return ""

    inversion = ' <span class="inversion">(инверсия)</span>' if spread < 0 else ""
    text = (f"Ставки США (Treasury, {esc(date_str)}): "
            f"3М {y3m:.2f}% · 2Y {y2:.2f}% · 10Y {y10:.2f}% · 2s10s {spread:+.2f}%{inversion}")
    return f'<div class="macro-banner">{text}</div>'


def macro_tile(title, date_str, value_str, delta):
    cls = "rise" if delta > 0 else ("fall" if delta < 0 else "")
    delta_txt = f"{delta:+.2f} п.п." if delta else "без изменений"
    return (f'<div class="macro-tile"><div class="t-title">{esc(title)} ({esc(date_str)})</div>'
            f'<div class="t-val">{value_str}</div>'
            f'<div class="t-delta {cls}">{delta_txt}</div></div>')


def build_fred_banner():
    macro = load_json(MACRO_JSON, None)
    if not macro:
        return ""

    tiles = []
    try:
        if macro.get("fed_funds_rate") is not None:
            rate = float(macro["fed_funds_rate"])
            delta = float(macro.get("fed_funds_delta") or 0)
            tiles.append(macro_tile("Ставка ФРС", macro.get("fed_funds_date", ""), f"{rate:.2f}%", delta))
    except (TypeError, ValueError):
        pass
    try:
        if macro.get("cpi_yoy") is not None:
            cpi = float(macro["cpi_yoy"])
            delta = float(macro.get("cpi_yoy_delta") or 0)
            tiles.append(macro_tile("Инфляция CPI (год к году)", macro.get("cpi_yoy_date", ""), f"{cpi:.2f}%", delta))
    except (TypeError, ValueError):
        pass

    if not tiles:
        return ""

    attribution = (
        '<div class="macro-attribution">Источник: <a href="https://fred.stlouisfed.org/" '
        'target="_blank" rel="noopener noreferrer">FRED, Federal Reserve Bank of St. Louis</a></div>'
    )
    return f'<div class="macro-tiles">{"".join(tiles)}</div>{attribution}'


def main():
    startups_data = load_json(STARTUPS_JSON, [])
    new_today = load_json(NEW_TODAY_JSON, [])
    ipo_new_today = load_json(IPO_NEW_TODAY_JSON, [])
    ipo_rows = load_csv(IPO_CSV)
    fund_rows = load_csv(FUNDAMENTALS_CSV)
    events_rows = load_csv(EVENTS_CSV)
    news_rows = load_csv(NEWS_CSV)
    formc_rows = load_csv(FORMC_CSV)
    macro_banner = build_macro_banner()
    fred_banner = build_fred_banner()

    # --- KPI ---
    startups_total = len(startups_data)
    startups_new = len(new_today) if new_today else sum(1 for r in startups_data if r.get("is_new"))

    ipo_total = len(ipo_rows)
    ipo_priced = sum(1 for r in ipo_rows if r.get("stage") == "Priced/IPO")

    fund_scored = sum(1 for r in fund_rows if (r.get("score") or "").strip() != "")

    events_count = len(events_rows)

    news_total = len(news_rows)
    news_mentions = sum(1 for r in news_rows if (r.get("mentions") or "").strip())

    def not_expired(r):
        return (r.get("is_expired") or "").strip().lower() != "true"

    formc_active = [r for r in formc_rows if not_expired(r)]

    print(f"[i] Стартапы: {startups_total} (нов. {startups_new})", file=sys.stderr)
    print(f"[i] IPO: {ipo_total} (priced {ipo_priced})", file=sys.stderr)
    print(f"[i] Фундаментал: скоренных {fund_scored}", file=sys.stderr)
    print(f"[i] События 8-K (30д): {events_count}", file=sys.stderr)
    print(f"[i] Новости: {news_total} (про мои {news_mentions})", file=sys.stderr)
    print(f"[i] Краудфандинг: активных {len(formc_active)} из {len(formc_rows)}", file=sys.stderr)
    if ipo_new_today:
        print(f"[i] IPO-событий сегодня: {len(ipo_new_today)}", file=sys.stderr)

    kpi_tiles = [
        kpi_tile("Стартапы", str(startups_total), f"нов. сегодня {startups_new}", "startups.html"),
        kpi_tile("IPO", f"{ipo_total} в пайплайне", f"priced {ipo_priced}", "ipo.html"),
        kpi_tile("Акции (фундаментал)", str(fund_scored), "скоренных", "fundamentals.html"),
        kpi_tile("События 8-K (30д)", str(events_count), "материальных филингов", "events.html"),
        kpi_tile("Новости", str(news_total), f"про мои компании {news_mentions}", "news.html"),
        kpi_tile("Краудфандинг (можно вложить)", str(len(formc_active)), "активных раундов", "crowdfunding.html"),
    ]

    # --- топ стартапов по score ---
    top_startups = sorted(startups_data, key=lambda r: r.get("score") or 0, reverse=True)[:5]
    startup_items = []
    for r in top_startups:
        new_tag = ' <span class="tag">NEW</span>' if r.get("is_new") else ""
        main_html = f'<a href="{esc(r.get("url",""))}" target="_blank" rel="noopener noreferrer">{esc(r.get("name",""))}</a>' \
                    f' <span class="tag">{esc(r.get("source",""))}</span>{new_tag}'
        sub_html = f'Score {esc(r.get("score",""))} · {esc(r.get("industry",""))}'
        startup_items.append(top_item(main_html, sub_html))

    # --- свежие IPO ---
    fresh_ipo = sorted(ipo_rows, key=lambda r: r.get("latest_filed") or "", reverse=True)[:5]
    ipo_items = []
    for r in fresh_ipo:
        main_html = f'{esc(r.get("company",""))} <span class="tag">{esc(r.get("stage",""))}</span>'
        sub_html = f'Цена {esc(fmt_price(r.get("offer_price")))} · {esc(r.get("latest_filed",""))}'
        ipo_items.append(top_item(main_html, sub_html))

    # --- топ акций по фундаментальному score ---
    scored_fund = [r for r in fund_rows if fund_score_num(r) is not None]
    top_stocks = sorted(scored_fund, key=fund_score_num, reverse=True)[:5]
    stock_items = []
    for r in top_stocks:
        label = r.get("ticker") or r.get("company", "")
        main_html = f'{esc(label)} <span class="tag">Score {esc(r.get("score",""))}</span>'
        sub_html = f'Выручка {esc(fmt_usd(r.get("latest_rev")))}'
        stock_items.append(top_item(main_html, sub_html))

    # --- последние события ---
    recent_events = sorted(events_rows, key=lambda r: r.get("date") or "", reverse=True)[:5]
    event_items = []
    for r in recent_events:
        main_html = f'{esc(r.get("company",""))}'
        sub_html = f'{esc(r.get("date",""))} · {esc(r.get("item_labels",""))}'
        event_items.append(top_item(main_html, sub_html))

    # --- последние новости ---
    recent_news = sorted(news_rows, key=lambda r: r.get("seendate") or "", reverse=True)[:5]
    news_items = []
    for r in recent_news:
        mentions = (r.get("mentions") or "").strip()
        badge = f'<span class="mention-badge">↳ {esc(mentions)}</span>' if mentions else ""
        main_html = f'<a href="{esc(r.get("url",""))}" target="_blank" rel="noopener noreferrer">{esc(r.get("title",""))}</a>{badge}'
        sub_html = f'{esc(r.get("seendate",""))} · {esc(r.get("domain",""))}'
        news_items.append(top_item(main_html, sub_html))

    # --- краудфандинг по ближайшему дедлайну ---
    soonest_formc = sorted(formc_active, key=lambda r: r.get("deadline") or "9999-99-99")[:5]
    formc_items = []
    for r in soonest_formc:
        main_html = f'{esc(r.get("company",""))} <span class="tag">{esc(r.get("platform",""))}</span>'
        sub_html = f'Цель {esc(fmt_usd(r.get("target_amount")))} · до {esc(r.get("deadline",""))}'
        formc_items.append(top_item(main_html, sub_html))

    top_blocks = [
        top_block("Топ стартапов по Score", "startups.html", startup_items),
        top_block("Свежие IPO", "ipo.html", ipo_items),
        top_block("Топ акций по фундаменталу", "fundamentals.html", stock_items),
        top_block("Последние события 8-K", "events.html", event_items),
        top_block("Последние новости", "news.html", news_items),
        top_block("Краудфандинг — ближайшие дедлайны", "crowdfunding.html", formc_items),
    ]

    html_out = (HTML_TEMPLATE
                .replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M"))
                .replace("__MACRO_BANNER__", macro_banner)
                .replace("__FRED_BANNER__", fred_banner)
                .replace("__KPI_TILES__", "\n".join(kpi_tiles))
                .replace("__TOP_BLOCKS__", "\n".join(top_blocks)))

    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[OK] -> {OUT_HTML}", file=sys.stderr)


if __name__ == "__main__":
    main()
