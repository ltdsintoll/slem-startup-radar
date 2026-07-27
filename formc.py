#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""formc.py — краудфандинг Reg CF (Form C/C-A): стартапы, куда может вложиться кто угодно."""
import sys, os, time, csv, json, re, argparse, datetime as dt
import urllib.parse
from xml.etree import ElementTree as ET

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

EFTS = "https://efts.sec.gov/LATEST/search-index"
ARCH = "https://www.sec.gov/Archives/edgar/data"
FORM_TYPES = ["C", "C/A"]
MAX_PER_FORM = 1000


def daterange(days):
    end = dt.date.today()
    return (end - dt.timedelta(days=days)).isoformat(), end.isoformat()


def cik_candidates(display_name, accession, ciks_field):
    """Порядок как в form_d.py: CIK из display_names -> префикс accession -> _source.ciks."""
    cands = []
    m = re.search(r"\(CIK\s*(\d+)\)", display_name or "")
    if m:
        cands.append(str(int(m.group(1))))
    try:
        cands.append(str(int(accession.split("-")[0])))
    except Exception:
        pass
    for x in ciks_field or []:
        try:
            cands.append(str(int(x)))
        except Exception:
            pass
    seen = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def search_form(form_type, startdt, enddt):
    out, frm = [], 0
    while len(out) < MAX_PER_FORM:
        q = urllib.parse.quote(form_type, safe="")
        url = f"{EFTS}?q=&forms={q}&startdt={startdt}&enddt={enddt}&from={frm}"
        raw = _get(url)
        data = json.loads(raw)
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
            display_name = (src.get("display_names") or [""])[0]
            accession, _, filename = hid.partition(":")
            filings.append({
                "cik": str(int(ciks[0])),
                "date": src.get("file_date", ""),
                "accession": accession,
                "filename": filename or "primary_doc.xml",
                "cik_candidates": cik_candidates(display_name, accession, ciks),
            })
            added += 1
        print(f"  -> {len(hits)} филингов, {added} новых", file=sys.stderr)
    return filings


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find_first(root, name):
    for el in root.iter():
        if local(el.tag) == name:
            return el
    return None


def text_of(el):
    return el.text.strip() if el is not None and el.text else ""


def fetch_and_parse(candidates, accession, filename):
    accession_nodash = accession.replace("-", "")
    for cik in candidates:
        url = f"{ARCH}/{cik}/{accession_nodash}/{filename}"
        try:
            raw = _get(url)
        except Exception:
            continue
        if raw is None:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue

        def g(name):
            return text_of(find_first(root, name))

        security_type = g("securityOfferedType")
        if security_type == "Other":
            other_desc = g("securityOfferedOtherDesc")
            if other_desc:
                security_type = f"Other ({other_desc})"

        return {
            "company": g("nameOfIssuer"),
            "website": g("issuerWebsite"),
            "jurisdiction": g("jurisdictionOrganization"),
            # именно companyName сразу после isCoIssuer — это площадка (intermediary):
            # в реальных Form C нет тега "nameOfIntermediary", схема кладёт имя портала
            # сюда, а рядом commissionCik/crdNumber — это регистрационные данные площадки
            "platform": g("companyName"),
            "target_amount": g("offeringAmount"),
            "max_amount": g("maximumOfferingAmount"),
            "deadline_raw": g("deadlineDate"),
            "security_type": security_type,
            "url": url,
        }, cik
    return None, None


def parse_deadline(raw):
    try:
        return dt.datetime.strptime(raw, "%m-%d-%Y").date().isoformat()
    except (ValueError, TypeError):
        return ""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Краудфандинг (Reg CF)</title>
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
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 6px; max-width: 700px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
  .counts { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin-bottom: 14px; }
  .counts b { font-size: 16px; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  #search {
    flex: 1 1 220px; min-width: 160px; padding: 8px 10px;
    border: 1px solid var(--border); border-radius: 6px; font-size: 14px;
  }
  main { padding: 12px 16px 40px; }
  .table-wrap { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; min-width: 800px; font-size: 13px; }
  thead th {
    position: sticky; top: 0; background: var(--bg-alt); border-bottom: 1px solid var(--border);
    text-align: left; padding: 9px 10px; cursor: pointer; white-space: nowrap; user-select: none;
  }
  thead th:hover { color: var(--accent); }
  tbody td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tbody tr:hover { background: var(--bg-alt); }
  .company-name { font-weight: 600; }
  .company-name a { color: inherit; text-decoration: none; }
  .company-name a:hover { color: var(--accent); text-decoration: underline; }
  .platform { color: var(--muted); }
  .expired-tag { color: #dc2626; font-size: 11px; font-weight: 600; margin-left: 6px; }
  .links a { font-size: 12px; color: var(--accent); text-decoration: none; white-space: nowrap; }
  .links a:hover { text-decoration: underline; }
  .empty { text-align: center; color: var(--muted); padding: 30px !important; }
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
    <a href="news.html">Новости</a>
    <a href="crowdfunding.html" class="active">Краудфандинг</a>
  </nav>
  <h1>Краудфандинг — можно вложить (Reg CF)</h1>
  <div class="subtitle">
    Стартапы, куда может вложиться любой (от ~$100) через указанную площадку;
    дедлайн — докуда открыт раунд.
  </div>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
    <span>Активных: <b>__ACTIVE_COUNT__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по компании, площадке, бумаге...">
  </div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-key="company">Компания</th>
          <th data-key="platform">Площадка</th>
          <th data-key="target_amount">Цель</th>
          <th data-key="max_amount">Максимум</th>
          <th data-key="deadline">Дедлайн</th>
          <th data-key="security_type">Бумага</th>
          <th>SEC</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>
<script>
const DATA = __DATA_JSON__;

let sortKey = "deadline";
let sortDir = 1;
let searchTerm = "";

function fmtUsd(v) {
  if (typeof v !== "number") return "—";
  return "$" + Math.round(v).toLocaleString("en-US");
}

function sortRows(rows) {
  if (!sortKey) return rows;
  return rows.slice().sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * sortDir;
    av = (av || "").toString().toLowerCase();
    bv = (bv || "").toString().toLowerCase();
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return 0;
  });
}

function render() {
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";

  let rows = DATA;
  if (searchTerm) {
    const t = searchTerm.toLowerCase();
    rows = rows.filter(r =>
      (r.company || "").toLowerCase().includes(t) ||
      (r.platform || "").toLowerCase().includes(t) ||
      (r.security_type || "").toLowerCase().includes(t)
    );
  }

  // активные — сверху по выбранной сортировке; истёкшие — отдельно внизу, всегда
  const active = sortRows(rows.filter(r => !r.is_expired));
  const expired = sortRows(rows.filter(r => r.is_expired));
  rows = active.concat(expired);

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
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
    if (row.website) {
      const a = document.createElement("a");
      a.href = row.website;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = row.company;
      tdCompany.appendChild(a);
    } else {
      tdCompany.textContent = row.company;
    }
    tr.appendChild(tdCompany);

    const tdPlatform = document.createElement("td");
    tdPlatform.className = "platform";
    tdPlatform.textContent = row.platform || "—";
    tr.appendChild(tdPlatform);

    const tdTarget = document.createElement("td");
    tdTarget.textContent = fmtUsd(row.target_amount);
    tr.appendChild(tdTarget);

    const tdMax = document.createElement("td");
    tdMax.textContent = fmtUsd(row.max_amount);
    tr.appendChild(tdMax);

    const tdDeadline = document.createElement("td");
    tdDeadline.textContent = row.deadline || "—";
    if (row.is_expired) {
      const tag = document.createElement("span");
      tag.className = "expired-tag";
      tag.textContent = "истёк";
      tdDeadline.appendChild(tag);
    }
    tr.appendChild(tdDeadline);

    const tdSecurity = document.createElement("td");
    tdSecurity.textContent = row.security_type || "";
    tr.appendChild(tdSecurity);

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

render();
</script>
</body>
</html>
"""


def build_html(rows):
    active_count = sum(1 for r in rows if not r["is_expired"])
    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__GENERATED_AT__", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(rows)))
            .replace("__ACTIVE_COUNT__", str(active_count))
            .replace("__DATA_JSON__", data_json))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--out", default="formc.csv")
    ap.add_argument("--html-out", default="public/crowdfunding.html")
    a = ap.parse_args()

    try:
        filings = collect_filings(a.days)
    except Exception as ex:
        print(f"[!] Ошибка доступа к SEC: {ex}", file=sys.stderr)
        return

    print(f"[i] Всего филингов (дедуп по _id): {len(filings)}", file=sys.stderr)

    # дедуп по CIK — берём самую свежую подачу (C/A перекрывает C)
    latest_by_cik = {}
    for f in filings:
        cik = f["cik"]
        prev = latest_by_cik.get(cik)
        if prev is None or f["date"] > prev["date"]:
            latest_by_cik[cik] = f

    today = dt.date.today().isoformat()
    rows = []
    for i, f in enumerate(latest_by_cik.values(), 1):
        parsed, used_cik = fetch_and_parse(f["cik_candidates"], f["accession"], f["filename"])
        if parsed is None:
            print(f"[{i}/{len(latest_by_cik)}] CIK {f['cik']} -> no-data/parse error", file=sys.stderr)
            time.sleep(0.2)
            continue

        deadline = parse_deadline(parsed["deadline_raw"])
        try:
            target_amount = float(parsed["target_amount"])
        except (TypeError, ValueError):
            target_amount = None
        try:
            max_amount = float(parsed["max_amount"])
        except (TypeError, ValueError):
            max_amount = None

        row = {
            "company": parsed["company"] or f["cik"],
            "website": parsed["website"],
            "platform": parsed["platform"],
            "target_amount": target_amount,
            "max_amount": max_amount,
            "deadline": deadline,
            "security_type": parsed["security_type"],
            "cik": f["cik"],
            "sec_url": parsed["url"],
            "is_expired": bool(deadline) and deadline < today,
        }
        rows.append(row)
        print(f"[{i}/{len(latest_by_cik)}] {row['company']} -> {row['platform']} (deadline {deadline or '?'})",
              file=sys.stderr)
        time.sleep(0.2)

    # активные раунды по ближайшему дедлайну сверху, истёкшие — в конец
    active_rows = sorted([r for r in rows if not r["is_expired"]], key=lambda r: r["deadline"] or "9999-99-99")
    expired_rows = sorted([r for r in rows if r["is_expired"]], key=lambda r: r["deadline"] or "", reverse=True)
    rows = active_rows + expired_rows

    active_count = len(active_rows)
    print(f"[i] Активных раундов: {active_count} из {len(rows)}", file=sys.stderr)

    from collections import Counter
    platform_counts = Counter(r["platform"] for r in active_rows if r["platform"])
    print(f"[i] По площадкам (активные): {dict(platform_counts.most_common())}", file=sys.stderr)

    cols = ["company", "website", "platform", "target_amount", "max_amount", "deadline",
            "security_type", "cik", "sec_url", "is_expired"]
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
