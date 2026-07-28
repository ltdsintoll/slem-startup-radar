#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""form_d.py — парсер свежих Form D с SEC EDGAR (частные раунды, в т.ч. стартапы)."""
import sys, time, csv, json, re, argparse, datetime as dt
from xml.etree import ElementTree as ET

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
ARCH = "https://www.sec.gov/Archives/edgar/data"
STARTUP_INDUSTRIES = {"Other Technology","Computers","Telecommunications","Biotechnology",
    "Pharmaceuticals","Other Health Care","Health Care","Internet","Other",
    "Manufacturing","Retailing","Business Services"}

# "Other"/"Business Services" в STARTUP_INDUSTRIES — самоотчёт filer'а по industryGroupType,
# ненадёжен: real estate trust или инвестфонд легко попадает в "Other". Отдельно ловим
# инвестиционные/трастовые vehicle по имени юрлица — word-boundary, регистронезависимо.
VEHICLE_NAME_RE = re.compile(r"\b(trust|dst|reit|fund|litigation|spv)\b", re.IGNORECASE)


def is_vehicle_name(entity):
    return bool(VEHICLE_NAME_RE.search(entity or ""))

def daterange(days):
    end = dt.date.today(); return (end - dt.timedelta(days=days)).isoformat(), end.isoformat()

def _cands(src, acc):
    names = src.get("display_names") or [""]; c = []
    m = re.search(r"\(CIK\s*(\d+)\)", names[0] if names else "")
    if m: c.append(str(int(m.group(1))))
    try: c.append(str(int(acc.split("-")[0])))
    except Exception: pass
    for x in src.get("ciks", []) or []:
        try: c.append(str(int(x)))
        except Exception: pass
    seen = set(); return [k for k in c if not (k in seen or seen.add(k))]

def search_form_d(startdt, enddt, max_results=100):
    out, frm = [], 0
    while len(out) < max_results:
        url = f"{EFTS}?q=&forms=D&startdt={startdt}&enddt={enddt}&from={frm}"
        data = json.loads(_get(url)); hits = data.get("hits", {}).get("hits", [])
        if not hits: break
        for h in hits:
            src = h.get("_source", {}); _id = h.get("_id", "")
            acc = _id.split(":")[0]; fname = _id.split(":")[1] if ":" in _id else "primary_doc.xml"
            out.append({"name": (src.get("display_names") or [""])[0], "date": src.get("file_date",""),
                        "ciks": _cands(src, acc), "accession": acc, "file": fname})
            if len(out) >= max_results: break
        frm += len(hits); time.sleep(0.2)
    return out

def _txt(n): return n.text.strip() if n is not None and n.text else ""
def _find(root, ln):
    for el in root.iter():
        if el.tag.rsplit("}",1)[-1] == ln: return el
    return None
def _findall(root, ln): return [el for el in root.iter() if el.tag.rsplit("}",1)[-1] == ln]

def parse_xml(xml_bytes, url=""):
    root = ET.fromstring(xml_bytes)
    def g(name): return _txt(_find(root, name))
    dfs = _find(root, "dateOfFirstSale")
    date_first_sale = _txt(_find(dfs, "value")) if dfs is not None else ""
    persons = []
    for rp in _findall(root, "relatedPersonInfo"):
        nm = _find(rp, "relatedPersonName")
        fn = _txt(_find(nm, "firstName")) if nm is not None else ""
        lnn = _txt(_find(nm, "lastName")) if nm is not None else ""
        rels = [_txt(r) for r in _findall(rp, "relationship")]
        full = " ".join(x for x in [fn, lnn] if x)
        if full: persons.append(f"{full} ({', '.join(rels)})" if rels else full)
    return {"entity": g("entityName"), "jurisdiction": g("jurisdictionOfInc"),
        "industry": g("industryGroupType"), "total_offering": g("totalOfferingAmount"),
        "total_sold": g("totalAmountSold"), "total_remaining": g("totalRemaining"),
        "min_investment": g("minimumInvestmentAccepted"), "num_investors": g("totalNumberAlreadyInvested"),
        "date_first_sale": date_first_sale, "related_persons": "; ".join(persons), "url": url}

def parse_primary_doc(ciks, accession, fname="primary_doc.xml"):
    nod = accession.replace("-", ""); last = None
    for c in ciks:
        u = f"{ARCH}/{c}/{nod}/{fname}"
        try: return parse_xml(_get(u), u)
        except Exception as ex: last = ex; continue
    raise last if last else RuntimeError("no CIK")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7); ap.add_argument("--max", type=int, default=60)
    ap.add_argument("--tech-only", action="store_true"); ap.add_argument("--out", default="form_d.csv")
    a = ap.parse_args()
    s, e = daterange(a.days); print(f"[i] Form D {s}..{e} (до {a.max})", file=sys.stderr)
    try: filings = search_form_d(s, e, a.max)
    except Exception as ex:
        print(f"[!] Ошибка доступа к SEC: {ex}", file=sys.stderr); return
    print(f"[i] Найдено филингов: {len(filings)}", file=sys.stderr)
    rows = []
    for i, f in enumerate(filings, 1):
        try: d = parse_primary_doc(f["ciks"], f["accession"], f["file"])
        except Exception as ex:
            print(f"  ! {f.get('name','?')}: {ex}", file=sys.stderr); continue
        if a.tech_only and (d["industry"] not in STARTUP_INDUSTRIES or is_vehicle_name(d["entity"])): continue
        d["filed"] = f["date"]; rows.append(d)
        print(f"  {i:>3}. {d['entity'][:38]:38} | {d['industry'][:16]:16} | offer={d['total_offering']:>12}", file=sys.stderr)
        time.sleep(0.2)
    cols = ["filed","entity","industry","jurisdiction","total_offering","total_sold","total_remaining",
            "min_investment","num_investors","date_first_sale","related_persons","url"]
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({k: r.get(k,"") for k in cols})
    print(f"[OK] Сохранено {len(rows)} строк -> {a.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
