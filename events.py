#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""events.py — свежие материальные события (8-K/6-K) по компаниям из fundamentals.csv."""
import sys, os, time, csv, json, argparse, datetime as dt

HEADERS = {"User-Agent": "Slem Invest Research (ltdsintoll@gmail.com)"}
try:
    import requests
    def _get(url):
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content
except Exception:
    import urllib.request, urllib.error
    def _get(url):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
EVENT_FORMS = {"8-K", "6-K"}
STATE_FILE = "state/events_seen.json"
NEW_TODAY_FILE = "public/events_new_today.json"
SIGNIFICANT_CODES = {"1.01", "1.05", "2.01", "2.02", "4.02", "5.02"}

ITEM_LABELS = {
    "1.01": "Существенное соглашение",
    "1.02": "Прекращение соглашения",
    "1.05": "Кибер-инцидент",
    "2.01": "Сделка с активами",
    "2.02": "Финансовые результаты",
    "2.03": "Новое обязательство",
    "3.01": "Делистинг/несоответствие",
    "4.01": "Смена аудитора",
    "4.02": "Пересмотр отчётности",
    "5.02": "Смена руководства/совета",
    "5.03": "Изменение устава",
    "7.01": "Reg FD",
    "8.01": "Прочее существенное",
    "9.01": "Приложения/финотчётность",
}


def load_universe(path):
    if not os.path.exists(path):
        print(f"[!] {path} не найден", file=sys.stderr)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if (r.get("cik") or "").strip():
            out.append({"cik": r["cik"], "company": r.get("company", ""), "ticker": r.get("ticker", "")})
    return out


def fetch_submissions(cik):
    url = SUBMISSIONS.format(cik=f"{int(cik):010d}")
    raw = _get(url)
    if raw is None:
        return None
    return json.loads(raw)


def parse_items(items_str, form):
    codes = [c.strip() for c in (items_str or "").split(",") if c.strip()]
    if not codes:
        return [], [form]
    labels = [ITEM_LABELS.get(c, c) for c in codes]
    return codes, labels


def extract_events(entry, submissions, cutoff_date):
    recent = (submissions or {}).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    accns = recent.get("accessionNumber", [])
    items = recent.get("items", [])

    events = []
    for i, form in enumerate(forms):
        if form not in EVENT_FORMS:
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if not filing_date or filing_date < cutoff_date:
            continue
        accession = accns[i] if i < len(accns) else ""
        primary_doc = docs[i] if i < len(docs) else ""
        items_str = items[i] if i < len(items) else ""
        if not accession or not primary_doc:
            continue

        codes, labels = parse_items(items_str, form)
        accession_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(entry['cik'])}/{accession_nodash}/{primary_doc}"

        events.append({
            "accession": accession,
            "date": filing_date,
            "company": entry["company"],
            "ticker": entry["ticker"],
            "cik": entry["cik"],
            "item_codes": ",".join(codes),
            "item_labels": ", ".join(labels),
            "url": url,
        })
    return events


def load_state(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def apply_new_tracking(events, state_path):
    """id события = accessionNumber. Baseline (пусто/нет файла) — фиксируем всё как seen,
    is_new не ставим никому. Иначе: новый accession -> is_new=true, first_seen=сегодня."""
    old_state = load_state(state_path)
    is_baseline = len(old_state) == 0
    today = dt.date.today().isoformat()
    new_state = dict(old_state)

    for e in events:
        accession = e["accession"]
        if accession in old_state:
            e["is_new"] = False
            e["first_seen"] = old_state[accession]["first_seen"]
        else:
            e["first_seen"] = today
            e["is_new"] = not is_baseline
            new_state[accession] = {"first_seen": today}

    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2, sort_keys=True)

    return is_baseline


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>События (8-K)</title>
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
  main { padding: 12px 16px 40px; }
  .table-wrap { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; min-width: 700px; font-size: 13px; }
  thead th {
    position: sticky; top: 0; background: var(--bg-alt); border-bottom: 1px solid var(--border);
    text-align: left; padding: 9px 10px; cursor: pointer; white-space: nowrap; user-select: none;
  }
  thead th:hover { color: var(--accent); }
  tbody td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tbody tr:hover { background: var(--bg-alt); }
  .company-name { font-weight: 600; }
  .ticker { color: var(--muted); font-size: 12px; }
  .links a { font-size: 12px; color: var(--accent); text-decoration: none; white-space: nowrap; }
  .links a:hover { text-decoration: underline; }
  .empty { text-align: center; color: var(--muted); padding: 30px !important; }
  .new-panel {
    font-size: 13px; background: #fff7ed; border: 1px solid #fed7aa;
    border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; max-width: 720px;
  }
  .new-panel b { color: #9a3412; }
  .new-badge {
    display: inline-block; padding: 1px 6px; border-radius: 8px;
    font-size: 10px; font-weight: 700; color: #fff; background: #dc2626;
    margin-left: 6px; vertical-align: middle;
  }
  .filter-btn {
    padding: 7px 12px; border: 1px solid var(--border); background: var(--bg);
    border-radius: 6px; cursor: pointer; font-size: 13px;
  }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
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
    <a href="events.html" class="active">События</a>
    <a href="news.html">Новости</a>
    <a href="crowdfunding.html">Краудфандинг</a>
  </nav>
  <h1>События (8-K)</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="new-panel" id="newPanel">
    <b>🆕 Новые события (<span id="newCount">0</span>)</b>
  </div>
  <div class="disclaimer">
    Материальные корпоративные события из 8-K SEC по отслеживаемым компаниям;
    коды 7.01/9.01 часто технические.
  </div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по компании, тикеру, событию...">
    <button class="filter-btn" id="newOnlyBtn">🆕 Только новые</button>
  </div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-key="date">Дата</th>
          <th data-key="company">Компания</th>
          <th data-key="ticker">Тикер</th>
          <th data-key="item_labels">Событие(я)</th>
          <th>Ссылка</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;

let sortKey = "date";
let sortDir = -1;
let searchTerm = "";
let onlyNew = false;

function render() {
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";

  let rows = DATA;
  if (onlyNew) rows = rows.filter(r => r.is_new);
  if (searchTerm) {
    const t = searchTerm.toLowerCase();
    rows = rows.filter(r =>
      (r.company || "").toLowerCase().includes(t) ||
      (r.ticker || "").toLowerCase().includes(t) ||
      (r.item_labels || "").toLowerCase().includes(t)
    );
  }

  if (sortKey) {
    rows = rows.slice().sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
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
    td.colSpan = 5;
    td.className = "empty";
    td.textContent = "Ничего не найдено";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");

    const tdDate = document.createElement("td");
    tdDate.textContent = row.date || "";
    tr.appendChild(tdDate);

    const tdCompany = document.createElement("td");
    tdCompany.className = "company-name";
    tdCompany.appendChild(document.createTextNode(row.company));
    if (row.is_new) {
      const badge = document.createElement("span");
      badge.className = "new-badge";
      badge.textContent = "NEW";
      tdCompany.appendChild(badge);
    }
    tr.appendChild(tdCompany);

    const tdTicker = document.createElement("td");
    tdTicker.className = "ticker";
    tdTicker.textContent = row.ticker || "—";
    tr.appendChild(tdTicker);

    const tdEvent = document.createElement("td");
    tdEvent.textContent = row.item_labels || "";
    tr.appendChild(tdEvent);

    const tdLinks = document.createElement("td");
    tdLinks.className = "links";
    const a = document.createElement("a");
    a.href = row.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "Филинг";
    tdLinks.appendChild(a);
    tr.appendChild(tdLinks);

    tbody.appendChild(tr);
  }
}

document.querySelectorAll("thead th[data-key]").forEach(th => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) { sortDir *= -1; } else { sortKey = key; sortDir = 1; }
    render();
  });
});

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

document.getElementById("newOnlyBtn").addEventListener("click", (e) => {
  onlyNew = !onlyNew;
  e.target.classList.toggle("active", onlyNew);
  render();
});

function renderNewPanel() {
  const newCount = DATA.filter(r => r.is_new).length;
  document.getElementById("newCount").textContent = newCount;
  if (newCount === 0) {
    document.getElementById("newPanel").innerHTML = "<b>🆕 Новые события (0)</b> — новых нет";
  }
}

renderNewPanel();
render();
</script>
</body>
</html>
"""


def build_html(rows):
    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__GENERATED_AT__", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(rows)))
            .replace("__DATA_JSON__", data_json))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--fundamentals-csv", default="fundamentals.csv")
    ap.add_argument("--out", default="events.csv")
    ap.add_argument("--html-out", default="public/events.html")
    a = ap.parse_args()

    universe = load_universe(a.fundamentals_csv)
    print(f"[i] Компаний в универсуме (с CIK): {len(universe)}", file=sys.stderr)

    cutoff_date = (dt.date.today() - dt.timedelta(days=a.days)).isoformat()

    events = []
    for i, entry in enumerate(universe, 1):
        name = entry["ticker"] or entry["company"]
        try:
            submissions = fetch_submissions(entry["cik"])
        except Exception as ex:
            print(f"[{i}/{len(universe)}] {name}: ошибка ({ex})", file=sys.stderr)
            submissions = None
        if submissions is None:
            print(f"[{i}/{len(universe)}] {name} -> no-data", file=sys.stderr)
        else:
            found = extract_events(entry, submissions, cutoff_date)
            events.extend(found)
            print(f"[{i}/{len(universe)}] {name} -> {len(found)} событий", file=sys.stderr)
        time.sleep(0.2)

    events.sort(key=lambda e: e["date"], reverse=True)
    print(f"[i] Всего событий за {a.days} дней: {len(events)}", file=sys.stderr)

    is_baseline = apply_new_tracking(events, STATE_FILE)
    new_events = [e for e in events if e["is_new"]]
    significant_new = [
        e for e in new_events
        if e["ticker"] or set(e["item_codes"].split(",")) & SIGNIFICANT_CODES
    ]
    new_today = [{
        "date": e["date"], "company": e["company"], "ticker": e["ticker"],
        "item_labels": e["item_labels"], "url": e["url"],
    } for e in significant_new]

    os.makedirs(os.path.dirname(NEW_TODAY_FILE), exist_ok=True)
    with open(NEW_TODAY_FILE, "w", encoding="utf-8") as f:
        json.dump(new_today, f, ensure_ascii=False, indent=2)

    if is_baseline:
        print("[i] events_seen.json был пуст — это baseline, events_new_today.json пустой", file=sys.stderr)
    print(f"[i] Новых событий: {len(new_events)} (значимых: {len(new_today)})", file=sys.stderr)

    cols = ["date", "company", "ticker", "cik", "item_codes", "item_labels", "url"]
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for e in events:
            w.writerow({k: e.get(k, "") for k in cols})
    print(f"[OK] Сохранено {len(events)} строк -> {a.out}", file=sys.stderr)

    os.makedirs(os.path.dirname(a.html_out), exist_ok=True)
    with open(a.html_out, "w", encoding="utf-8") as f:
        f.write(build_html(events))
    print(f"[OK] -> {a.html_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
