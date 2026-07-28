#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dashboard.py — единый локальный дашборд по стартапам (Form D + YC + Product Hunt)
в один index.html, с прозрачным скорингом приоритизации (не прогноз успеха — эвристика)."""
import csv, json, math, os, re, sys
from datetime import date, datetime

FORMD_ENRICHED_CSV = "startups_enriched.csv"
FORMD_PLAIN_CSV = "startups.csv"
YC_CSV = "yc.csv"
PRODUCT_HUNT_CSV = "product_hunt.csv"
OUT_HTML = "public/startups.html"
STARTUPS_DATA_JSON = "public/startups_data.json"
STATE_FILE = "state/seen.json"
NEW_TODAY_FILE = "public/new_today.json"

ACCESSION_RE = re.compile(r"/data/\d+/(\d+)/")

# --- скоринг: веса компонентов (сумма = 1.0) ---
# Pedigree был снижен, Traction/Freshness подняты: при старых весах (T.40 P.25 F.20 C.15)
# YC почти всегда получает pedigree=95 (YC_PEDIGREE_BASE+ACTIVE_BONUS) и completeness=100
# (сайт есть почти всегда), это давало YC жёсткий "пол" по score независимо от traction —
# топ-20 на реальных данных был 20/0 в пользу YC, лучший Form D был на 21-м месте с разницей
# в 1 балл. Pedigree/completeness — почти не различающие компании внутри YC сигналы, поэтому
# их вес снижен в пользу traction (единственный по-настоящему сравнимый между источниками
# сигнал) и freshness.
W_TRACTION = 0.45
W_PEDIGREE = 0.15
W_FRESHNESS = 0.25
W_COMPLETENESS = 0.15

# масштаб раунда решает: $250k->30, $2M->55, $20M->80, >=$200M->100
FORMD_TRACTION_ANCHORS = [
    (math.log10(250_000), 30), (math.log10(2_000_000), 55),
    (math.log10(20_000_000), 80), (math.log10(200_000_000), 100),
]
# log10(team_size): 1->25, 10->55, 50->80, >=200->100
YC_TRACTION_ANCHORS = [
    (math.log10(1), 25), (math.log10(10), 55), (math.log10(50), 80), (math.log10(200), 100),
]
# num_investors: 0->30, 3->50, 10->75, >=25->95 (плюс бонус за total_offering ниже)
FORMD_PEDIGREE_ANCHORS = [(0, 30), (3, 50), (10, 75), (25, 95)]
FORMD_PEDIGREE_MEGA_ROUND_BONUS = 15  # +15 если total_offering >= порога ниже
FORMD_PEDIGREE_MEGA_ROUND_THRESHOLD = 50_000_000

YC_PEDIGREE_BASE = 70
YC_PEDIGREE_ACTIVE_BONUS = 25
YC_PEDIGREE_INACTIVE_PENALTY = 40

# Product Hunt: engagement = votes + 3*comments (комментарий — более сильный сигнал
# вовлечённости, чем апвоут), log10-интерполяция как у Form D/YC traction. Без реальных
# PH-данных для калибровки (нет токена на момент правки) — round-number якоря, не измерено.
PH_TRACTION_ANCHORS = [
    (math.log10(5), 20), (math.log10(30), 45), (math.log10(150), 70), (math.log10(800), 95),
]
PH_COMMENT_WEIGHT = 3

SEASON_RANK = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}
BATCH_RE = re.compile(r"^(Winter|Spring|Summer|Fall)\s+(\d{4})$", re.IGNORECASE)


def interp_clamped(x, anchors):
    """Кусочно-линейная интерполяция по заданным опорным точкам (x возрастает),
    за пределами крайних точек — продолжение по наклону последнего сегмента, клэмп [0,100]."""
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    if x <= xs[0]:
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        y = ys[0] + slope * (x - xs[0])
    elif x >= xs[-1]:
        slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        y = ys[-1] + slope * (x - xs[-1])
    else:
        y = ys[-1]
        for i in range(len(xs) - 1):
            if xs[i] <= x <= xs[i + 1]:
                t = (x - xs[i]) / (xs[i + 1] - xs[i])
                y = ys[i] + t * (ys[i + 1] - ys[i])
                break
    return max(0.0, min(100.0, y))


def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def fmt_amount(raw):
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return "", -1
    return f"${n:,}", n


def batch_key(batch):
    m = BATCH_RE.match(batch or "")
    if not m:
        return None
    season, year = m.group(1).lower(), int(m.group(2))
    return (year, SEASON_RANK[season])


def score_of(p, t, f, c):
    return round(W_PEDIGREE * p + W_TRACTION * t + W_FRESHNESS * f + W_COMPLETENESS * c)


def score_of_ph(t, f, c):
    """Product Hunt не даёт сигнала pedigree (нет данных о фаундерах/финансировании) —
    "н/д", не выдуманное число. Вес pedigree пропорционально перераспределён на T/F/C
    (те же глобальные веса, без изменения W_* констант), как fundamentals.py уже делает
    для компонентов с недостающими данными."""
    total_weight = W_TRACTION + W_FRESHNESS + W_COMPLETENESS
    return round((W_TRACTION * t + W_FRESHNESS * f + W_COMPLETENESS * c) / total_weight)


def formd_id(sec_url):
    m = ACCESSION_RE.search(sec_url or "")
    return f"formd:{m.group(1)}" if m else f"formd:{sec_url}"


def yc_id(yc_url):
    slug = (yc_url or "").rstrip("/").split("/")[-1]
    return f"yc:{slug}" if slug else f"yc:{yc_url}"


def ph_id(post_id):
    return f"ph:{post_id}"


def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def apply_new_tracking(rows, state_path):
    """Помечает каждую строку is_new/first_seen на основе state/seen.json.
    Первый запуск (пустой/отсутствующий seen.json) — baseline: все строки помечаются
    seen, но is_new=False для всех (нет истории, значит нет и "нового")."""
    old_state = load_state(state_path)
    is_baseline = len(old_state) == 0
    today = date.today().isoformat()
    new_state = dict(old_state)

    for row in rows:
        rid = row["id"]
        if rid in old_state:
            row["is_new"] = False
            row["first_seen"] = old_state[rid]["first_seen"]
        else:
            row["first_seen"] = today
            row["is_new"] = not is_baseline
            new_state[rid] = {"first_seen": today, "name": row["name"], "source": row["source"]}

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2, sort_keys=True)

    return is_baseline


def load_formd():
    path = FORMD_ENRICHED_CSV if os.path.exists(FORMD_ENRICHED_CSV) else FORMD_PLAIN_CSV
    if not os.path.exists(path):
        print(f"[i] нет данных Form D (ни {FORMD_ENRICHED_CSV}, ни {FORMD_PLAIN_CSV}) — пропускаю", file=sys.stderr)
        return []

    today = date.today()
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            display, amount_raw = fmt_amount(r.get("total_offering", ""))
            founders = r.get("related_persons", "")
            num_investors = to_int(r.get("num_investors", ""))
            confidence = r.get("confidence", "") or ""
            guessed_website = r.get("guessed_website", "") or ""
            enriched_desc = (r.get("description", "") or "").strip()
            industry = r.get("industry", "") or ""

            # freshness: 0 дней = 100, 90+ дней = 0, линейно
            try:
                filed_date = datetime.strptime(r.get("filed", ""), "%Y-%m-%d").date()
                days = max(0, (today - filed_date).days)
                freshness = max(0.0, min(100.0, 100 - (days / 90) * 100))
            except ValueError:
                freshness = 0.0

            traction = 0.0
            if amount_raw > 0:
                traction = interp_clamped(math.log10(amount_raw), FORMD_TRACTION_ANCHORS)

            pedigree = interp_clamped(num_investors, FORMD_PEDIGREE_ANCHORS)
            if amount_raw >= FORMD_PEDIGREE_MEGA_ROUND_THRESHOLD:
                pedigree += FORMD_PEDIGREE_MEGA_ROUND_BONUS
            pedigree = max(0.0, min(100.0, pedigree))
            completeness = {"high": 100, "medium": 50}.get(confidence, 0)

            p, t, fr, c = round(pedigree), round(traction), round(freshness), completeness
            sec_url = r.get("url", "")

            rows.append({
                "id": formd_id(sec_url),
                "source": "Form D",
                "name": r.get("entity", ""),
                "description": enriched_desc or industry,
                "industry": industry,
                "amount_display": display,
                "amount_sort": amount_raw,
                "extra": f"founders: {founders}" if founders else "",
                "location": r.get("jurisdiction", ""),
                "website": "",
                "guessed_website": guessed_website,
                "confidence": confidence,
                "sec_url": sec_url,
                "yc_url": "",
                "ph_url": "",
                "score": score_of(p, t, fr, c),
                "score_p": p, "score_t": t, "score_f": fr, "score_c": c,
            })
    print(f"[i] Form D: {len(rows)} строк из {path}", file=sys.stderr)
    return rows


def load_yc(path):
    if not os.path.exists(path):
        print(f"[i] {path} не найден — пропускаю YC", file=sys.stderr)
        return []

    raw_rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            raw_rows.append(r)

    # freshness батча: самый свежий из ЗАГРУЖЕННЫХ батчей = 100, каждый предыдущий -15
    present_batches = sorted(
        {r.get("batch", "") for r in raw_rows if batch_key(r.get("batch", "")) is not None},
        key=batch_key, reverse=True,
    )
    batch_rank = {b: i for i, b in enumerate(present_batches)}

    rows = []
    for r in raw_rows:
        batch = r.get("batch", "")
        team = to_int(r.get("team_size", ""))
        status = r.get("status", "") or ""
        website = r.get("website", "") or ""
        extra = f"{batch} · team {team}" if team else batch

        rank = batch_rank.get(batch)
        freshness = max(0, 100 - 15 * rank) if rank is not None else 0

        traction = interp_clamped(math.log10(team), YC_TRACTION_ANCHORS) if team > 0 else 0.0

        pedigree = YC_PEDIGREE_BASE
        if status == "Active":
            pedigree += YC_PEDIGREE_ACTIVE_BONUS
        elif status == "Inactive":
            pedigree -= YC_PEDIGREE_INACTIVE_PENALTY
        pedigree = max(0, min(100, pedigree))

        completeness = 100 if website else 0

        p, t, fr, c = round(pedigree), round(traction), round(freshness), completeness
        yc_url = r.get("yc_url", "")

        rows.append({
            "id": yc_id(yc_url),
            "source": "YC",
            "name": r.get("name", ""),
            "description": r.get("one_liner", ""),
            "industry": r.get("industry", ""),
            "amount_display": "",
            "amount_sort": -1,
            "extra": extra,
            "location": r.get("location", ""),
            "website": website,
            "guessed_website": "",
            "confidence": "",
            "sec_url": "",
            "yc_url": yc_url,
            "ph_url": "",
            "score": score_of(p, t, fr, c),
            "score_p": p, "score_t": t, "score_f": fr, "score_c": c,
        })
    print(f"[i] YC: {len(rows)} строк из {path}", file=sys.stderr)
    return rows


def load_product_hunt(path):
    if not os.path.exists(path):
        print(f"[i] {path} не найден — пропускаю Product Hunt", file=sys.stderr)
        return []

    with open(path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    today = date.today()
    rows = []
    for r in raw_rows:
        votes = to_int(r.get("votes", ""))
        comments = to_int(r.get("comments", ""))
        website = r.get("website", "") or ""
        makers = r.get("makers", "") or ""
        topics = r.get("topics", "") or ""

        engagement = votes + PH_COMMENT_WEIGHT * comments
        traction = interp_clamped(math.log10(engagement), PH_TRACTION_ANCHORS) if engagement > 0 else 0.0

        # freshness — та же линейная деградация по дням, что у Form D (0 дн.=100, 90+=0);
        # с --days 7 на входе она всё равно будет у потолка, это ожидаемо
        try:
            posted = datetime.strptime(r.get("created_at", ""), "%Y-%m-%d").date()
            days = max(0, (today - posted).days)
            freshness = max(0.0, min(100.0, 100 - (days / 90) * 100))
        except ValueError:
            freshness = 0.0

        completeness = 100 if (website and makers) else (50 if (website or makers) else 0)

        t, fr, c = round(traction), round(freshness), completeness
        post_id = r.get("id", "")
        extra_bits = []
        if votes: extra_bits.append(f"👍{votes}")
        if comments: extra_bits.append(f"💬{comments}")
        extra = " · ".join(extra_bits)

        rows.append({
            "id": ph_id(post_id),
            "source": "Product Hunt",
            "name": r.get("name", ""),
            "description": r.get("description", "") or r.get("tagline", ""),
            "industry": topics,
            "amount_display": "",
            "amount_sort": votes if votes else -1,
            "extra": extra,
            "location": "",
            "website": website,
            "guessed_website": "",
            "confidence": "",
            "sec_url": "",
            "yc_url": "",
            "ph_url": r.get("url", ""),
            "score": score_of_ph(t, fr, c),
            # pedigree — н/д для Product Hunt (нет данных о фаундерах/финансировании),
            # None -> JSON null -> "н/д" в тултипе (см. tdScore.title в JS ниже)
            "score_p": None, "score_t": t, "score_f": fr, "score_c": c,
        })
    print(f"[i] Product Hunt: {len(rows)} строк из {path}", file=sys.stderr)
    return rows


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Startup Radar</title>
<style>
  :root {
    --border: #e2e2e7;
    --muted: #6b6b76;
    --bg: #ffffff;
    --bg-alt: #f8f8fa;
    --accent: #2451c7;
    --formd: #b45309;
    --yc: #ea580c;
    --ph: #da552f;
    --green: #16a34a;
    --gray: #9ca3af;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #16161a;
    background: var(--bg-alt);
  }
  header {
    padding: 20px 16px 14px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
  }
  nav.pages { margin-bottom: 10px; font-size: 13px; }
  nav.pages a {
    color: var(--muted); text-decoration: none; margin-right: 14px; padding-bottom: 2px;
  }
  nav.pages a.active { color: var(--accent); font-weight: 600; border-bottom: 2px solid var(--accent); }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
  .score-disclaimer {
    font-size: 12px; color: var(--muted); background: var(--bg-alt);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; margin-bottom: 12px; max-width: 720px;
  }
  .score-disclaimer b { color: #16161a; }
  .new-panel {
    font-size: 13px; background: #fff7ed; border: 1px solid #fed7aa;
    border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; max-width: 720px;
  }
  .new-panel b { color: #9a3412; }
  .new-list { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 6px; }
  .new-chip {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; background: #fff; border: 1px solid #fed7aa; color: #9a3412;
  }
  .new-badge {
    display: inline-block; padding: 1px 6px; border-radius: 8px;
    font-size: 10px; font-weight: 700; color: #fff; background: #dc2626;
    margin-left: 6px; vertical-align: middle;
  }
  .counts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin-bottom: 14px; }
  .counts b { font-size: 16px; }
  .controls {
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
  }
  #search {
    flex: 1 1 220px;
    min-width: 160px;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
  }
  .filter-btn {
    padding: 7px 12px;
    border: 1px solid var(--border);
    background: var(--bg);
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
  }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  main { padding: 12px 16px 40px; }
  .table-wrap {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
  }
  table { border-collapse: collapse; width: 100%; min-width: 860px; font-size: 13px; }
  thead th {
    position: sticky; top: 0;
    background: var(--bg-alt);
    border-bottom: 1px solid var(--border);
    text-align: left;
    padding: 9px 10px;
    cursor: pointer;
    white-space: nowrap;
    user-select: none;
  }
  thead th:hover { color: var(--accent); }
  thead th .arrow { font-size: 10px; opacity: 0.6; margin-left: 3px; }
  tbody td {
    padding: 9px 10px;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  tbody tr:hover { background: var(--bg-alt); }
  .badge {
    display: inline-block; padding: 2px 7px; border-radius: 10px;
    font-size: 11px; font-weight: 600; color: #fff; white-space: nowrap;
  }
  .badge.formd { background: var(--formd); }
  .badge.yc { background: var(--yc); }
  .badge.ph { background: var(--ph); }
  .company-name { font-weight: 600; }
  .sub { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .score-cell { cursor: help; }
  .score-num { font-weight: 700; font-size: 14px; }
  .score-bar {
    width: 46px; height: 5px; background: var(--border); border-radius: 3px;
    margin-top: 4px; overflow: hidden;
  }
  .score-bar-fill { height: 100%; background: var(--accent); }
  .site-badge {
    display: inline-block; padding: 2px 7px; border-radius: 10px;
    font-size: 11px; font-weight: 600; color: #fff; text-decoration: none;
    margin-right: 6px; white-space: nowrap;
  }
  .site-badge.high { background: var(--green); }
  .site-badge.medium { background: var(--gray); }
  .site-caveat { font-size: 10px; color: var(--muted); display: block; margin: 2px 0 4px; }
  .links a {
    margin-right: 8px; font-size: 12px; color: var(--accent);
    text-decoration: none; white-space: nowrap;
  }
  .links a:hover { text-decoration: underline; }
  .empty { text-align: center; color: var(--muted); padding: 30px !important; }
  @media (max-width: 640px) {
    h1 { font-size: 19px; }
    .counts { gap: 10px; }
  }
</style>
</head>
<body>
<header>
  <nav class="pages">
    <a href="index.html">Обзор</a>
    <a href="startups.html" class="active">Стартапы</a>
    <a href="ipo.html">IPO Pipeline</a>
    <a href="fundamentals.html">Фундаментал</a>
    <a href="events.html">События</a>
    <a href="news.html">Новости</a>
    <a href="crowdfunding.html">Краудфандинг</a>
  </nav>
  <h1>Startup Radar</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="new-panel" id="newPanel">
    <b>🆕 Новое со вчера (<span id="newCount">0</span>)</b>
    <div class="new-list" id="newList"></div>
  </div>
  <div class="score-disclaimer">
    <b>Score</b> — эвристика для приоритизации, НЕ прогноз успеха; веса настраиваемые
    (P __W_P__ · T __W_T__ · F __W_F__ · C __W_C__). Наведи на балл — раскладка по компонентам.
  </div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
    <span>Form D: <b>__FORMD_COUNT__</b></span>
    <span>YC: <b>__YC_COUNT__</b></span>
    <span>Product Hunt: <b>__PH_COUNT__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по названию, описанию, отрасли...">
    <button class="filter-btn active" data-source="all">Все</button>
    <button class="filter-btn" data-source="Form D">Form D</button>
    <button class="filter-btn" data-source="YC">YC</button>
    <button class="filter-btn" data-source="Product Hunt">Product Hunt</button>
    <button class="filter-btn" id="newOnlyBtn">🆕 Только новые</button>
  </div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-key="source">Источник</th>
          <th data-key="name">Компания</th>
          <th data-key="description">Чем занимается</th>
          <th data-key="industry">Отрасль</th>
          <th data-key="amount_sort">Сумма/Батч/Апвоуты</th>
          <th data-key="score">Score</th>
          <th data-key="location">Локация</th>
          <th>Ссылки</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;

let sortKey = "score";
let sortDir = -1;
let sourceFilter = "all";
let searchTerm = "";
let onlyNew = false;

function searchUrl(base, name) {
  return base + encodeURIComponent(name);
}

function buildLinks(row) {
  const links = [];
  if (row.website) links.push({ label: "Site", href: row.website });
  if (row.sec_url) links.push({ label: "SEC", href: row.sec_url });
  if (row.yc_url) links.push({ label: "YC", href: row.yc_url });
  if (row.ph_url) links.push({ label: "Product Hunt", href: row.ph_url });
  links.push({ label: "Google", href: searchUrl("https://www.google.com/search?q=", row.name) });
  links.push({ label: "LinkedIn", href: searchUrl("https://www.linkedin.com/search/results/all/?keywords=", row.name) });
  links.push({ label: "Crunchbase", href: searchUrl("https://www.crunchbase.com/textsearch?q=", row.name) });
  return links;
}

function render() {
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";

  let rows = DATA.filter(r => sourceFilter === "all" || r.source === sourceFilter);
  if (onlyNew) rows = rows.filter(r => r.is_new);
  if (searchTerm) {
    const t = searchTerm.toLowerCase();
    rows = rows.filter(r =>
      (r.name || "").toLowerCase().includes(t) ||
      (r.description || "").toLowerCase().includes(t) ||
      (r.industry || "").toLowerCase().includes(t)
    );
  }

  if (sortKey) {
    rows = rows.slice().sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (typeof av === "number" && typeof bv === "number") {
        return (av - bv) * sortDir;
      }
      av = (av || "").toString().toLowerCase();
      bv = (bv || "").toString().toLowerCase();
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return 0;
    });
  }

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "empty";
    td.textContent = "Ничего не найдено";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");

    const tdSource = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + (row.source === "Form D" ? "formd" : row.source === "YC" ? "yc" : "ph");
    badge.textContent = row.source;
    tdSource.appendChild(badge);
    tr.appendChild(tdSource);

    const tdName = document.createElement("td");
    const nameDiv = document.createElement("div");
    nameDiv.className = "company-name";
    nameDiv.appendChild(document.createTextNode(row.name));
    if (row.is_new) {
      const newBadge = document.createElement("span");
      newBadge.className = "new-badge";
      newBadge.textContent = "NEW";
      nameDiv.appendChild(newBadge);
    }
    tdName.appendChild(nameDiv);
    if (row.extra) {
      const sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent = row.extra;
      tdName.appendChild(sub);
    }
    tr.appendChild(tdName);

    const tdDesc = document.createElement("td");
    if (row.source === "Form D" && row.guessed_website && (row.confidence === "high" || row.confidence === "medium")) {
      const siteLink = document.createElement("a");
      siteLink.href = row.guessed_website;
      siteLink.target = "_blank";
      siteLink.rel = "noopener noreferrer";
      siteLink.className = "site-badge " + row.confidence;
      siteLink.textContent = row.confidence === "high" ? "Site ✓" : "Site?";
      tdDesc.appendChild(siteLink);
      if (row.confidence === "medium") {
        const caveat = document.createElement("span");
        caveat.className = "site-caveat";
        caveat.textContent = "сайт не подтверждён — угадан по названию";
        tdDesc.appendChild(caveat);
      } else {
        tdDesc.appendChild(document.createElement("br"));
      }
    }
    tdDesc.appendChild(document.createTextNode(row.description || ""));
    tr.appendChild(tdDesc);

    const tdIndustry = document.createElement("td");
    tdIndustry.textContent = row.industry || "";
    tr.appendChild(tdIndustry);

    const tdAmount = document.createElement("td");
    tdAmount.textContent = row.source === "Form D" ? (row.amount_display || "") : (row.extra || "");
    tr.appendChild(tdAmount);

    const tdScore = document.createElement("td");
    tdScore.className = "score-cell";
    const pedigreeLabel = typeof row.score_p === "number" ? row.score_p : "н/д";
    tdScore.title = `Pedigree ${pedigreeLabel} · Traction ${row.score_t} · Freshness ${row.score_f} · Completeness ${row.score_c}`;
    const scoreNum = document.createElement("div");
    scoreNum.className = "score-num";
    scoreNum.textContent = row.score;
    tdScore.appendChild(scoreNum);
    const bar = document.createElement("div");
    bar.className = "score-bar";
    const fill = document.createElement("div");
    fill.className = "score-bar-fill";
    fill.style.width = Math.max(0, Math.min(100, row.score)) + "%";
    bar.appendChild(fill);
    tdScore.appendChild(bar);
    tr.appendChild(tdScore);

    const tdLocation = document.createElement("td");
    tdLocation.textContent = row.location || "";
    tr.appendChild(tdLocation);

    const tdLinks = document.createElement("td");
    tdLinks.className = "links";
    for (const link of buildLinks(row)) {
      const a = document.createElement("a");
      a.href = link.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = link.label;
      tdLinks.appendChild(a);
    }
    tr.appendChild(tdLinks);

    tbody.appendChild(tr);
  }
}

document.querySelectorAll("thead th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) {
      sortDir *= -1;
    } else {
      sortKey = key;
      sortDir = 1;
    }
    render();
  });
});

document.querySelectorAll(".filter-btn[data-source]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn[data-source]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    sourceFilter = btn.dataset.source;
    render();
  });
});

document.getElementById("newOnlyBtn").addEventListener("click", (e) => {
  onlyNew = !onlyNew;
  e.target.classList.toggle("active", onlyNew);
  render();
});

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

function renderNewPanel() {
  const newItems = DATA.filter(r => r.is_new);
  document.getElementById("newCount").textContent = newItems.length;
  const list = document.getElementById("newList");
  list.innerHTML = "";
  if (newItems.length === 0) {
    list.textContent = "новых нет";
    return;
  }
  for (const r of newItems) {
    const chip = document.createElement("span");
    chip.className = "new-chip";
    chip.textContent = `${r.name} (${r.source})`;
    list.appendChild(chip);
  }
}

renderNewPanel();
render();
</script>
</body>
</html>
"""


def main():
    formd_rows = load_formd()
    yc_rows = load_yc(YC_CSV)
    ph_rows = load_product_hunt(PRODUCT_HUNT_CSV)
    data = formd_rows + yc_rows + ph_rows

    is_baseline = apply_new_tracking(data, STATE_FILE)
    new_items = [r for r in data if r["is_new"]]

    new_today = [{
        "id": r["id"], "name": r["name"], "source": r["source"], "score": r["score"],
        "industry": r["industry"], "description": r["description"],
        "url": r["sec_url"] or r["yc_url"] or r["ph_url"], "first_seen": r["first_seen"],
    } for r in new_items]

    os.makedirs(os.path.dirname(NEW_TODAY_FILE), exist_ok=True)
    with open(NEW_TODAY_FILE, "w", encoding="utf-8") as f:
        json.dump(new_today, f, ensure_ascii=False, indent=2)

    if is_baseline:
        print("[i] seen.json был пуст — это baseline, new_today.json пустой", file=sys.stderr)
    print(f"[i] Новых со вчера: {len(new_today)}", file=sys.stderr)

    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (HTML_TEMPLATE
            .replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(data)))
            .replace("__FORMD_COUNT__", str(len(formd_rows)))
            .replace("__YC_COUNT__", str(len(yc_rows)))
            .replace("__PH_COUNT__", str(len(ph_rows)))
            .replace("__W_P__", str(W_PEDIGREE))
            .replace("__W_T__", str(W_TRACTION))
            .replace("__W_F__", str(W_FRESHNESS))
            .replace("__W_C__", str(W_COMPLETENESS))
            .replace("__DATA_JSON__", data_json))

    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    startups_data = [{
        "name": r["name"], "source": r["source"], "score": r["score"],
        "industry": r["industry"], "description": r["description"],
        "url": r["sec_url"] or r["yc_url"] or r["ph_url"], "is_new": r["is_new"], "first_seen": r["first_seen"],
    } for r in data]
    os.makedirs(os.path.dirname(STARTUPS_DATA_JSON), exist_ok=True)
    with open(STARTUPS_DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(startups_data, f, ensure_ascii=False)

    print(f"[OK] {len(data)} строк ({len(formd_rows)} Form D + {len(yc_rows)} YC + {len(ph_rows)} Product Hunt) -> {OUT_HTML}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
