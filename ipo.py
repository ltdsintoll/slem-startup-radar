#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ipo.py — трекер IPO-пайплайна SEC EDGAR: кто подаёт S-1/F-1 и кто уже прайсит 424B4."""
import sys, os, time, csv, json, re, argparse, datetime as dt
import urllib.parse

HEADERS = {"User-Agent": "Slem Invest Research (ltdsintoll@gmail.com)"}
try:
    import requests
    def _get(url):
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status(); return r.content
except Exception:
    import urllib.request
    def _get(url):
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp: return resp.read()

EFTS = "https://efts.sec.gov/LATEST/search-index"
FORM_TYPES = ["S-1", "S-1/A", "424B4", "F-1", "F-1/A"]
MAX_PER_FORM = 500  # защита от бесконечной пагинации, реальный объём на 30 дней намного меньше


def daterange(days):
    end = dt.date.today()
    return (end - dt.timedelta(days=days)).isoformat(), end.isoformat()


def extract_name(display_name):
    name = re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", display_name or "").strip()
    m = re.match(r"^(.*?)\s*\(([A-Z0-9]+(?:,\s*[A-Z0-9]+)*)\)$", name)
    if m and m.group(1).strip():
        name = m.group(1).strip()
    return name


def search_form(form_type, startdt, enddt):
    out, frm = [], 0
    while len(out) < MAX_PER_FORM:
        q = urllib.parse.quote(form_type, safe="")
        url = f"{EFTS}?q=&forms={q}&startdt={startdt}&enddt={enddt}&from={frm}"
        data = json.loads(_get(url))
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        out.extend(hits)
        frm += len(hits)
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        time.sleep(0.2)
        if frm >= total:
            break
    return out


def collect_filings(days):
    startdt, enddt = daterange(days)
    seen_ids = set()
    filings = []
    for form_type in FORM_TYPES:
        print(f"[i] Ищу {form_type} за {startdt}..{enddt}", file=sys.stderr)
        hits = search_form(form_type, startdt, enddt)
        added = 0
        for h in hits:
            hid = h.get("_id", "")
            if hid in seen_ids:
                continue
            seen_ids.add(hid)
            src = h.get("_source", {})
            ciks = src.get("ciks") or []
            if not ciks:
                continue
            filings.append({
                "cik": str(int(ciks[0])),
                "name": extract_name((src.get("display_names") or [""])[0]),
                "form": src.get("form", form_type),
                "date": src.get("file_date", ""),
            })
            added += 1
        print(f"  -> {len(hits)} филингов, {added} новых", file=sys.stderr)
    return filings


def group_by_company(filings):
    companies = {}
    for f in filings:
        c = companies.setdefault(f["cik"], {"cik": f["cik"], "filings": []})
        c["filings"].append({"date": f["date"], "form": f["form"], "name": f["name"]})

    rows = []
    for c in companies.values():
        dates = [flt["date"] for flt in c["filings"] if flt["date"]]
        forms_seen = sorted({flt["form"] for flt in c["filings"]})
        latest_filed = max(dates) if dates else ""
        # имя берём с самого свежего филинга (компании иногда переименовываются)
        name = next((flt["name"] for flt in c["filings"] if flt["date"] == latest_filed), c["filings"][0]["name"])

        if "424B4" in forms_seen:
            stage = "Priced/IPO"
        elif "S-1/A" in forms_seen or "F-1/A" in forms_seen:
            stage = "Amending"
        else:
            stage = "Filed"

        rows.append({
            "company": name,
            "cik": c["cik"],
            "stage": stage,
            "first_filed": min(dates) if dates else "",
            "latest_filed": latest_filed,
            "forms_seen": ",".join(forms_seen),
            "sec_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c['cik']}&type=&dateb=&owner=include&count=40",
        })
    return rows


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IPO Pipeline</title>
<style>
  :root {
    --border: #e2e2e7; --muted: #6b6b76; --bg: #ffffff; --bg-alt: #f8f8fa;
    --accent: #2451c7; --filed: #6b7280; --amending: #d97706; --priced: #16a34a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #16161a; background: var(--bg-alt);
  }
  header { padding: 20px 16px 14px; background: var(--bg); border-bottom: 1px solid var(--border); }
  nav.pages { margin-bottom: 10px; font-size: 13px; }
  nav.pages a {
    color: var(--muted); text-decoration: none; margin-right: 14px; padding-bottom: 2px;
  }
  nav.pages a.active { color: var(--accent); font-weight: 600; border-bottom: 2px solid var(--accent); }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
  .disclaimer {
    font-size: 12px; color: var(--muted); background: var(--bg-alt);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; margin-bottom: 12px; max-width: 720px;
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
  .table-wrap { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; min-width: 720px; font-size: 13px; }
  thead th {
    position: sticky; top: 0; background: var(--bg-alt); border-bottom: 1px solid var(--border);
    text-align: left; padding: 9px 10px; cursor: pointer; white-space: nowrap; user-select: none;
  }
  thead th:hover { color: var(--accent); }
  tbody td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tbody tr:hover { background: var(--bg-alt); }
  .company-name { font-weight: 600; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; color: #fff; white-space: nowrap;
  }
  .badge.stage-filed { background: var(--filed); }
  .badge.stage-amending { background: var(--amending); }
  .badge.stage-priced { background: var(--priced); }
  .links a { margin-right: 8px; font-size: 12px; color: var(--accent); text-decoration: none; white-space: nowrap; }
  .links a:hover { text-decoration: underline; }
  .empty { text-align: center; color: var(--muted); padding: 30px !important; }
  @media (max-width: 640px) { h1 { font-size: 19px; } .counts { gap: 10px; } }
</style>
</head>
<body>
<header>
  <nav class="pages">
    <a href="index.html">Стартапы</a>
    <a href="ipo.html" class="active">IPO Pipeline</a>
  </nav>
  <h1>IPO Pipeline</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="disclaimer">
    IPO-пайплайн по публичным филингам SEC; подача S-1 не гарантирует и не датирует IPO.
  </div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
    <span>Filed: <b>__FILED_COUNT__</b></span>
    <span>Amending: <b>__AMENDING_COUNT__</b></span>
    <span>Priced/IPO: <b>__PRICED_COUNT__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по названию...">
    <button class="filter-btn active" data-stage="all">Все</button>
    <button class="filter-btn" data-stage="Filed">Filed</button>
    <button class="filter-btn" data-stage="Amending">Amending</button>
    <button class="filter-btn" data-stage="Priced/IPO">Priced</button>
  </div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-key="company">Компания</th>
          <th data-key="stage">Stage</th>
          <th data-key="first_filed">Первый филинг</th>
          <th data-key="latest_filed">Последний филинг</th>
          <th data-key="forms_seen">Формы</th>
          <th>Ссылки</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;

let sortKey = "latest_filed";
let sortDir = -1;
let stageFilter = "all";
let searchTerm = "";

function searchUrl(base, q) { return base + encodeURIComponent(q); }

function stageClass(stage) {
  if (stage === "Priced/IPO") return "stage-priced";
  if (stage === "Amending") return "stage-amending";
  return "stage-filed";
}

function buildLinks(row) {
  return [
    { label: "SEC", href: row.sec_url },
    { label: "Google", href: searchUrl("https://www.google.com/search?q=", row.company) },
    { label: "IPO news", href: searchUrl("https://www.google.com/search?q=", row.company + " IPO") },
  ];
}

function render() {
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";

  let rows = DATA.filter(r => stageFilter === "all" || r.stage === stageFilter);
  if (searchTerm) {
    const t = searchTerm.toLowerCase();
    rows = rows.filter(r => (r.company || "").toLowerCase().includes(t));
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
    td.colSpan = 6;
    td.className = "empty";
    td.textContent = "Ничего не найдено";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");

    const tdCompany = document.createElement("td");
    tdCompany.className = "company-name";
    tdCompany.textContent = row.company;
    tr.appendChild(tdCompany);

    const tdStage = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + stageClass(row.stage);
    badge.textContent = row.stage;
    tdStage.appendChild(badge);
    tr.appendChild(tdStage);

    const tdFirst = document.createElement("td");
    tdFirst.textContent = row.first_filed || "";
    tr.appendChild(tdFirst);

    const tdLatest = document.createElement("td");
    tdLatest.textContent = row.latest_filed || "";
    tr.appendChild(tdLatest);

    const tdForms = document.createElement("td");
    tdForms.textContent = row.forms_seen || "";
    tr.appendChild(tdForms);

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
    if (sortKey === key) { sortDir *= -1; } else { sortKey = key; sortDir = 1; }
    render();
  });
});

document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    stageFilter = btn.dataset.stage;
    render();
  });
});

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

render();
</script>
</body>
</html>
"""


def build_html(rows):
    counts = {"Filed": 0, "Amending": 0, "Priced/IPO": 0}
    for r in rows:
        counts[r["stage"]] = counts.get(r["stage"], 0) + 1

    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__GENERATED_AT__", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(rows)))
            .replace("__FILED_COUNT__", str(counts["Filed"]))
            .replace("__AMENDING_COUNT__", str(counts["Amending"]))
            .replace("__PRICED_COUNT__", str(counts["Priced/IPO"]))
            .replace("__DATA_JSON__", data_json))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="ipo.csv")
    ap.add_argument("--html-out", default="public/ipo.html")
    a = ap.parse_args()

    try:
        filings = collect_filings(a.days)
    except Exception as ex:
        print(f"[!] Ошибка доступа к SEC: {ex}", file=sys.stderr)
        return

    print(f"[i] Всего филингов (дедуп): {len(filings)}", file=sys.stderr)
    rows = group_by_company(filings)
    rows.sort(key=lambda r: r["latest_filed"], reverse=True)
    print(f"[i] Компаний в пайплайне: {len(rows)}", file=sys.stderr)

    cols = ["company", "cik", "stage", "first_filed", "latest_filed", "forms_seen", "sec_url"]
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"[OK] Сохранено {len(rows)} строк -> {a.out}", file=sys.stderr)

    os.makedirs(os.path.dirname(a.html_out), exist_ok=True)
    with open(a.html_out, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print(f"[OK] -> {a.html_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
