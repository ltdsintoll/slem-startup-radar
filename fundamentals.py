#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fundamentals.py — фундаментал по свежим Priced/IPO из ipo.csv через XBRL SEC companyfacts."""
import sys, os, time, csv, json, argparse
from datetime import date, datetime

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

XBRL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REVENUE_CONCEPTS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
ANNUAL_FORMS = {"10-K", "20-F"}

W_GROWTH = 0.35
W_PROFIT = 0.30
W_BALANCE = 0.25
W_DATA = 0.10

MULT_HIGH = 1.0
MULT_MED = 0.85

GROWTH_ANCHORS = [(-0.20, 0), (0.0, 40), (0.30, 80), (1.00, 100)]
PROFIT_ANCHORS = [(-0.5, 0), (0.0, 50), (0.20, 100)]
BALANCE_ANCHORS = [(0.3, 100), (0.6, 60), (0.9, 20)]  # леверидж, чем выше — тем ниже баллы


def interp_clamped(x, anchors):
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


def load_priced_companies(path):
    if not os.path.exists(path):
        print(f"[!] {path} не найден", file=sys.stderr)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if r.get("stage") == "Priced/IPO" and (r.get("cik") or "").strip():
            out.append(r)
    return out


def fetch_companyfacts(cik):
    url = XBRL.format(cik=f"{int(cik):010d}")
    raw = _get(url)
    if raw is None:
        return None
    return json.loads(raw)


def _duration_days(entry):
    try:
        start = date.fromisoformat(entry["start"])
        end = date.fromisoformat(entry["end"])
    except (KeyError, ValueError, TypeError):
        return None
    return (end - start).days


def annual_periods(entries):
    """Годовые точки: форма 10-K/20-F с длительностью ~год, либо fp=FY. Дедуп по end (латест filed)."""
    best_by_end = {}
    for e in entries:
        end = e.get("end")
        if not end:
            continue
        days = _duration_days(e)
        is_annual_form = e.get("form") in ANNUAL_FORMS and days is not None and 330 <= days <= 400
        is_fy = e.get("fp") == "FY"
        if not (is_annual_form or is_fy):
            continue
        prev = best_by_end.get(end)
        if prev is None or e.get("filed", "") > prev.get("filed", ""):
            best_by_end[end] = e
    return sorted(best_by_end.values(), key=lambda e: e["end"], reverse=True)


def latest_instant(entries):
    dated = [e for e in entries if e.get("end")]
    if not dated:
        return None
    return max(dated, key=lambda e: e["end"])


def extract_metrics(facts):
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})

    latest_rev = prev_rev = None
    for concept in REVENUE_CONCEPTS:
        units = gaap.get(concept, {}).get("units", {}).get("USD")
        if not units:
            continue
        periods = annual_periods(units)
        if periods:
            latest_rev = periods[0]["val"]
            prev_rev = periods[1]["val"] if len(periods) > 1 else None
            break

    net_income = None
    ni_units = gaap.get("NetIncomeLoss", {}).get("units", {}).get("USD")
    if ni_units:
        periods = annual_periods(ni_units)
        if periods:
            net_income = periods[0]["val"]

    def instant(concept):
        units = gaap.get(concept, {}).get("units", {}).get("USD")
        if not units:
            return None
        e = latest_instant(units)
        return e["val"] if e else None

    assets = instant("Assets")
    liabilities = instant("Liabilities")
    equity = instant("StockholdersEquity")
    cash = instant("CashAndCashEquivalentsAtCarryingValue")

    return {
        "latest_rev": latest_rev, "prev_rev": prev_rev, "net_income": net_income,
        "assets": assets, "liabilities": liabilities, "equity": equity, "cash": cash,
    }


def compute_score(rev_growth, net_margin, leverage, data_found_count):
    components = []  # (weight, score)

    growth_score = None
    if rev_growth is not None:
        growth_score = interp_clamped(rev_growth, GROWTH_ANCHORS)
        components.append((W_GROWTH, growth_score))

    profit_score = None
    if net_margin is not None:
        profit_score = interp_clamped(net_margin, PROFIT_ANCHORS)
        components.append((W_PROFIT, profit_score))

    if leverage is not None:
        balance_score = interp_clamped(leverage, BALANCE_ANCHORS)
    else:
        balance_score = 50.0
    components.append((W_BALANCE, balance_score))

    data_score = (data_found_count / 3) * 100
    components.append((W_DATA, data_score))

    total_weight = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / total_weight if total_weight else 0
    return round(score), growth_score, profit_score, round(balance_score), round(data_score)


def build_row(ipo_row, facts):
    m = extract_metrics(facts)
    latest_rev, prev_rev, net_income = m["latest_rev"], m["prev_rev"], m["net_income"]
    assets, liabilities, cash = m["assets"], m["liabilities"], m["cash"]

    rev_growth = (latest_rev / prev_rev - 1) if (latest_rev is not None and prev_rev) else None
    net_margin = (net_income / latest_rev) if (net_income is not None and latest_rev) else None
    leverage = (liabilities / assets) if (liabilities is not None and assets) else None

    # ядро P&L (выручка + чистая прибыль) обязательно — компанию без него не скорим,
    # даже если известен баланс: иначе SPAC без выручки может обогнать компанию с реальными
    # цифрами только за счёт низкого левериджа
    has_core = latest_rev is not None and net_income is not None

    if not has_core:
        score = ""
        confidence = "low"
        growth_score = profit_score = balance_score = data_score = None
    else:
        data_found = 3 if leverage is not None else 2
        confidence = "high" if leverage is not None else "med"
        base_score, growth_score, profit_score, balance_score, data_score = compute_score(
            rev_growth, net_margin, leverage, data_found)
        mult = MULT_HIGH if confidence == "high" else MULT_MED
        score = round(base_score * mult)

    try:
        offer_price = float(ipo_row.get("offer_price") or "")
    except ValueError:
        offer_price = ""
    price_confidence = ipo_row.get("price_confidence") or ""

    return {
        "company": ipo_row["company"],
        "cik": ipo_row["cik"],
        "latest_rev": latest_rev if latest_rev is not None else "",
        "rev_growth": rev_growth if rev_growth is not None else "",
        "net_income": net_income if net_income is not None else "",
        "net_margin": net_margin if net_margin is not None else "",
        "leverage": leverage if leverage is not None else "",
        "cash": cash if cash is not None else "",
        "score": score,
        "data_confidence": confidence,
        "score_growth": round(growth_score) if growth_score is not None else None,
        "score_profit": round(profit_score) if profit_score is not None else None,
        "score_balance": balance_score,
        "score_data": data_score,
        "offer_price": offer_price,
        "offer_price_confidence": price_confidence,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Фундаментал IPO</title>
<style>
  :root {
    --border: #e2e2e7; --muted: #6b6b76; --bg: #ffffff; --bg-alt: #f8f8fa;
    --accent: #2451c7; --green: #16a34a; --red: #dc2626; --gray: #9ca3af;
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
  table { border-collapse: collapse; width: 100%; min-width: 860px; font-size: 13px; }
  thead th {
    position: sticky; top: 0; background: var(--bg-alt); border-bottom: 1px solid var(--border);
    text-align: left; padding: 9px 10px; cursor: pointer; white-space: nowrap; user-select: none;
  }
  thead th:hover { color: var(--accent); }
  tbody td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tbody tr:hover { background: var(--bg-alt); }
  .company-name { font-weight: 600; }
  .score-cell { cursor: help; font-weight: 700; }
  .score-na { color: var(--muted); font-style: italic; white-space: nowrap; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .badge {
    display: inline-block; padding: 2px 7px; border-radius: 10px;
    font-size: 11px; font-weight: 600; color: #fff; white-space: nowrap;
  }
  .badge.high { background: var(--green); }
  .badge.med { background: #d97706; }
  .badge.low { background: var(--gray); }
  .empty { text-align: center; color: var(--muted); padding: 30px !important; }
  @media (max-width: 640px) { h1 { font-size: 19px; } .counts { gap: 10px; } }
</style>
</head>
<body>
<header>
  <nav class="pages">
    <a href="index.html">Стартапы</a>
    <a href="ipo.html">IPO Pipeline</a>
    <a href="fundamentals.html" class="active">Фундаментал IPO</a>
  </nav>
  <h1>Свежие IPO — фундаментал</h1>
  <div class="meta">Обновлено: __GENERATED_AT__</div>
  <div class="disclaimer">
    Фундаментальный снимок из XBRL SEC. НЕ рекомендация к покупке; свежие IPO часто убыточны
    by design — смотри на цифры, не только на балл. Компании без раскрытой выручки/прибыли
    (часто SPAC и до-выручные) не скорятся — показаны отдельно внизу.
  </div>
  <div class="counts">
    <span>Всего: <b>__TOTAL__</b></span>
    <span>Данные high: <b>__HIGH_COUNT__</b></span>
    <span>med: <b>__MED_COUNT__</b></span>
    <span>low: <b>__LOW_COUNT__</b></span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Поиск по названию...">
  </div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-key="company">Компания</th>
          <th data-key="offer_price">Цена IPO</th>
          <th data-key="latest_rev">Выручка</th>
          <th data-key="rev_growth">Рост выручки %</th>
          <th data-key="net_income">Чистая прибыль</th>
          <th data-key="net_margin">Маржа %</th>
          <th data-key="leverage">Леверидж</th>
          <th data-key="score">Score</th>
          <th data-key="data_confidence">Данные</th>
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
let searchTerm = "";

function fmtUsd(v) {
  if (typeof v !== "number") return "—";
  return (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString("en-US");
}
function fmtPct(v) {
  if (typeof v !== "number") return "—";
  return (v * 100).toFixed(1) + "%";
}
function fmtRatio(v) {
  if (typeof v !== "number") return "—";
  return v.toFixed(2);
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
    rows = rows.filter(r => (r.company || "").toLowerCase().includes(t));
  }

  // скоренные (реальный score) — всегда сверху, без ядра P&L — отдельно внизу,
  // независимо от того, какая колонка сейчас активна для сортировки
  const scored = sortRows(rows.filter(r => typeof r.score === "number"));
  const unscored = sortRows(rows.filter(r => typeof r.score !== "number"));
  rows = scored.concat(unscored);

  if (rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 9;
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

    const tdPrice = document.createElement("td");
    tdPrice.textContent = typeof row.offer_price === "number" ? "$" + row.offer_price.toFixed(2) : "—";
    tr.appendChild(tdPrice);

    const tdRev = document.createElement("td");
    tdRev.textContent = fmtUsd(row.latest_rev);
    tr.appendChild(tdRev);

    const tdGrowth = document.createElement("td");
    tdGrowth.textContent = fmtPct(row.rev_growth);
    if (typeof row.rev_growth === "number") tdGrowth.className = row.rev_growth >= 0 ? "pos" : "neg";
    tr.appendChild(tdGrowth);

    const tdNI = document.createElement("td");
    tdNI.textContent = fmtUsd(row.net_income);
    if (typeof row.net_income === "number") tdNI.className = row.net_income >= 0 ? "pos" : "neg";
    tr.appendChild(tdNI);

    const tdMargin = document.createElement("td");
    tdMargin.textContent = fmtPct(row.net_margin);
    if (typeof row.net_margin === "number") tdMargin.className = row.net_margin >= 0 ? "pos" : "neg";
    tr.appendChild(tdMargin);

    const tdLev = document.createElement("td");
    tdLev.textContent = fmtRatio(row.leverage);
    tr.appendChild(tdLev);

    const tdScore = document.createElement("td");
    if (typeof row.score === "number") {
      tdScore.className = "score-cell";
      tdScore.textContent = row.score;
      tdScore.title = `Growth ${row.score_growth ?? "—"} · Profit ${row.score_profit ?? "—"} · Balance ${row.score_balance} · Data ${row.score_data}`;
    } else {
      tdScore.className = "score-na";
      tdScore.textContent = "н/д (нет финансов)";
    }
    tr.appendChild(tdScore);

    const tdConf = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge " + row.data_confidence;
    badge.textContent = row.data_confidence;
    tdConf.appendChild(badge);
    tr.appendChild(tdConf);

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
    counts = {"high": 0, "med": 0, "low": 0}
    for r in rows:
        counts[r["data_confidence"]] += 1

    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    return (HTML_TEMPLATE
            .replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__TOTAL__", str(len(rows)))
            .replace("__HIGH_COUNT__", str(counts["high"]))
            .replace("__MED_COUNT__", str(counts["med"]))
            .replace("__LOW_COUNT__", str(counts["low"]))
            .replace("__DATA_JSON__", data_json))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipo-csv", default="ipo.csv")
    ap.add_argument("--out", default="fundamentals.csv")
    ap.add_argument("--html-out", default="public/fundamentals.html")
    a = ap.parse_args()

    companies = load_priced_companies(a.ipo_csv)
    print(f"[i] Priced/IPO компаний с CIK: {len(companies)}", file=sys.stderr)

    rows = []
    for i, ipo_row in enumerate(companies, 1):
        name, cik = ipo_row["company"], ipo_row["cik"]
        try:
            facts = fetch_companyfacts(cik)
        except Exception as ex:
            print(f"[{i}/{len(companies)}] {name}: ошибка ({ex})", file=sys.stderr)
            facts = None
        if facts is None:
            print(f"[{i}/{len(companies)}] {name} -> no-data", file=sys.stderr)
        row = build_row(ipo_row, facts)
        print(f"[{i}/{len(companies)}] {name} -> score={row['score']} ({row['data_confidence']})",
              file=sys.stderr)
        rows.append(row)
        time.sleep(0.2)

    # скоренные (реальный score) — сверху по убыванию; без ядра P&L — отдельно внизу
    scored = sorted((r for r in rows if isinstance(r["score"], int)), key=lambda r: r["score"], reverse=True)
    unscored = sorted((r for r in rows if not isinstance(r["score"], int)), key=lambda r: r["company"])
    rows = scored + unscored
    print(f"[i] Скоренных: {len(scored)}, без ядра P&L (н/д): {len(unscored)}", file=sys.stderr)

    cols = ["company", "cik", "latest_rev", "rev_growth", "net_income", "net_margin",
            "leverage", "cash", "score", "data_confidence"]
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
