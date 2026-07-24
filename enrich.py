#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enrich.py — обогащение startups.csv (Form D) веб-данными через угадывание собственного домена
компании (без поисковика, без бана по IP): генерим кандидаты <slug>.<tld>, стучимся напрямую."""
import csv, html, os, re, sys
import urllib.error, urllib.request

IN_CSV = "startups.csv"
OUT_CSV = "startups_enriched.csv"
METHOD = "domain-guess"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TIMEOUT = 5
TLDS = [".com", ".io", ".ai", ".co", ".tech", ".app"]
MAX_CANDIDATES = 12

SUFFIX_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "ltd", "limited", "co",
    "company", "holdings", "holding", "technologies", "technology", "instruments",
    "liability", "group",
}

PARKED_MARKERS = [
    "domain for sale", "buy this domain", "is for sale", "domain is parked",
    "this domain is parked", "parking page", "domain parking", "hugedomains",
    "godaddy.com/domains", "sedo.com", "afternic", "dan.com", "namecheap.com/domains",
    "the domain has expired", "future home of something quite cool",
]


def normalize_words(entity):
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", entity)
    words = [w.lower() for w in cleaned.split() if w]
    kept = [w for w in words if w not in SUFFIX_WORDS]
    return kept or words


def build_candidates(entity):
    words = normalize_words(entity)
    if not words:
        return [], ""

    slug_all = "".join(words)
    slug_hyphen = "-".join(words)
    slug_first2 = "".join(words[:2])
    slug_first = words[0]

    slug_order = [slug_all, slug_all, slug_all, slug_all, slug_all, slug_all,
                  slug_hyphen, slug_hyphen, slug_first2, slug_first2, slug_first, slug_first]
    tld_order = [".com", ".io", ".ai", ".co", ".tech", ".app", ".com", ".io", ".com", ".io", ".com", ".io"]

    seen = set()
    candidates = []
    for slug, tld in zip(slug_order, tld_order):
        if not slug:
            continue
        domain = slug + tld
        if domain in seen:
            continue
        seen.add(domain)
        candidates.append(domain)
        if len(candidates) >= MAX_CANDIDATES:
            break

    return candidates, words


META_RE = re.compile(r"<meta\s+([^>]*?)/?>", re.I | re.S)
ATTR_RE = re.compile(r'([a-zA-Z_:-]+)\s*=\s*"(.*?)"|([a-zA-Z_:-]+)\s*=\s*\'(.*?)\'', re.S)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def parse_meta(body):
    tags = {}
    for m in META_RE.finditer(body):
        attrs = {}
        for a in ATTR_RE.finditer(m.group(1)):
            if a.group(1):
                attrs[a.group(1).lower()] = a.group(2)
            else:
                attrs[a.group(3).lower()] = a.group(4)
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key and "content" in attrs:
            tags[key] = attrs["content"]
    return tags


def fetch(domain):
    req = urllib.request.Request(f"https://{domain}", headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            return None
        ctype = resp.headers.get("Content-Type", "").lower()
        if ctype and "html" not in ctype and "text" not in ctype:
            return None
        body = resp.read(300_000).decode("utf-8", errors="replace")
    if "<" not in body:
        return None

    title_m = TITLE_RE.search(body)
    title = html.unescape(title_m.group(1)).strip() if title_m else ""
    meta = parse_meta(body)
    description = meta.get("description") or meta.get("og:description") or ""
    description = html.unescape(description).strip()
    site_name = html.unescape(meta.get("og:site_name", "")).strip()

    parked_haystack = (title + " " + description + " " + body[:3000]).lower()
    if any(marker in parked_haystack for marker in PARKED_MARKERS):
        return None

    # only title/meta/og feed the word-match check below — raw body HTML is full of
    # boilerplate ("<!doctype") and self-referential links (href="https://<domain>/...")
    # that would leak the slug back in and make the match trivially true again
    match_text = (title + " " + description + " " + site_name).lower()
    return {"title": title, "description": description, "site_name": site_name, "match_text": match_text}


def guess(entity):
    candidates, distinctive_words = build_candidates(entity)
    checked = []

    for domain in candidates:
        checked.append(domain)
        try:
            page = fetch(domain)
        except Exception:
            continue
        if page is None:
            continue

        # match against title/meta text only (never the domain/body — see fetch()); whole-word
        # regex so short tokens like "co" don't hit substrings like "document"
        matched_words = [w for w in distinctive_words if re.search(rf"\b{re.escape(w)}\b", page["match_text"])]
        full_match = bool(distinctive_words) and len(matched_words) == len(distinctive_words)
        # single leftover word (or a dictionary-word coincidence) is too weak to call "high"
        # even on a full match — e.g. "Elixir" alone also names an unrelated CCM company
        multi_word = len(distinctive_words) >= 2
        confidence = "high" if (full_match and multi_word) else "medium"
        description = page["description"] or page["title"] or page["site_name"]
        return {
            "guessed_website": f"https://{domain}",
            "description": description,
            "confidence": confidence,
            "method": METHOD,
            "checked_domains": ";".join(checked),
        }

    return {
        "guessed_website": "",
        "description": "",
        "confidence": "low",
        "method": METHOD,
        "checked_domains": ";".join(checked),
    }


def main():
    if not os.path.exists(IN_CSV):
        print(f"[!] {IN_CSV} не найден", file=sys.stderr)
        return

    with open(IN_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        base_cols = reader.fieldnames
        rows = list(reader)

    print(f"[i] Компаний для обогащения: {len(rows)}", file=sys.stderr)

    counts = {"high": 0, "medium": 0, "low": 0}
    out_rows = []
    for i, row in enumerate(rows, 1):
        entity = row.get("entity", "")
        try:
            enrichment = guess(entity)
        except Exception as ex:
            print(f"  ! {entity}: {ex}", file=sys.stderr)
            enrichment = {"guessed_website": "", "description": "", "confidence": "low",
                          "method": METHOD, "checked_domains": ""}
        counts[enrichment["confidence"]] += 1
        print(f"[{i}/{len(rows)}] {entity} -> {enrichment['confidence']} ({enrichment['guessed_website'] or '—'})",
              file=sys.stderr)
        out_rows.append({**row, **enrichment})

    cols = base_cols + ["guessed_website", "description", "confidence", "method", "checked_domains"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in cols})

    print(f"[OK] {len(out_rows)} строк -> {OUT_CSV}", file=sys.stderr)
    print(f"[summary] high={counts['high']} medium={counts['medium']} low={counts['low']}", file=sys.stderr)


if __name__ == "__main__":
    main()
