#!/usr/bin/env python3
"""Fetch and parse UDC (Urban Data Challenge) past award results.

Reads the official results pages on urbandata-challenge.jp, normalises
every awarded work into a record, and writes the canonical DB as JSON
(+ CSV mirror) into the project folder.

Usage:
    python3 udc_fetch.py [--out DIR] [--no-fetch]

Output (default ~/workspace/projects/udc-awards/):
    udc_awards.json   canonical DB (list of records)
    udc_awards.csv    same records as CSV
    validation.json   per-year source-row counts vs parsed counts

Politeness: one request per second at most, results pages only,
raw HTML cached under <out>/raw/ so re-runs do not re-hit the site.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://urbandata-challenge.jp"
RESULT_URLS = {
    2015: f"{BASE}/2015/prize",
    2016: f"{BASE}/2016/udc2016prize",
    2017: f"{BASE}/2017/udc2017prize",
    2018: f"{BASE}/highlight/udc2018prize",
    2019: f"{BASE}/highlight/udc2019prize",
    2020: f"{BASE}/news/udc2020prize",
    2021: f"{BASE}/news/udc2021prize",
    2022: f"{BASE}/news/udc2022prize",
    2023: f"{BASE}/news/udc2023prize",
    2024: f"{BASE}/news/udc2024prize",
    2025: f"{BASE}/news/udc2025prize",
}
TEXT_YEARS = {2015, 2016}  # narrative format, no tables

MEDALS = ("金賞", "銀賞", "銅賞")
AWARD_RE = re.compile(
    r"^(金賞|銀賞|銅賞|最優秀賞|優秀賞|特別賞|奨励賞|学生奨励賞|.*賞)$"
)
HEADER_WORDS = {
    "賞", "テーマ", "タイプ", "作品名", "チーム名",
    "代表者名／チーム名", "エントリ番号", "部門", "拠点名",
}
DIVISION_ALIASES = {
    "ビジネス・プロ部門": "ビジネス・プロフェッショナル部門",
    "ビジネスプロフェッショナル部門": "ビジネス・プロフェッショナル部門",
}
TYPE_DEPT_RE = re.compile(
    r"^(アプリケーション|データ|アイデア|アクティビティ|ソリューション)部門$"
)


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def clean_title(s):
    s = norm(s)
    s = re.sub(r"\s*(作品ページ|作品詳細)$", "", s)
    return s.strip()


def clean_team(s):
    s = norm(s)
    s = re.sub(r"\s*/\s*(受賞者?コメント.*)$", "", s)
    s = re.sub(r"\s+(受賞者?コメント.*)$", "", s)
    return s.strip()


def norm_division(s):
    s = norm(s).strip("【】")
    return DIVISION_ALIASES.get(s, s)


# ---------------------------------------------------------------- HTML utils

class TableParser(HTMLParser):
    """Extract tables as lists of rows of cell strings."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._cur = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur = []
        elif tag == "tr" and self._cur is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""
        elif tag == "br" and self._cell is not None:
            self._cell += " / "

    def handle_endtag(self, tag):
        if tag == "table" and self._cur is not None:
            self.tables.append(self._cur)
            self._cur = None
        elif tag == "tr" and self._row is not None:
            self._cur.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(norm(self._cell))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data


def split_segments(html_text):
    """Split a page into ordered ('text', lines) / ('table', rows) segments."""
    import html as _h

    parts = re.split(r"(<table.*?</table>)", html_text, flags=re.S)
    segments = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<table"):
            tp = TableParser()
            tp.feed(part)
            for t in tp.tables:
                segments.append(("table", t))
        else:
            txt = re.sub(r"<script.*?</script>", "", part, flags=re.S)
            txt = re.sub(r"<style.*?</style>", "", txt, flags=re.S)
            txt = re.sub(r"<[^>]+>", "\n", txt)
            txt = _h.unescape(txt)
            lines = [norm(l) for l in txt.split("\n")]
            segments.append(("text", [l for l in lines if l]))
    return segments


# ------------------------------------------------------------------- parsing

class State:
    def __init__(self):
        self.division = "一般部門"
        self.section = ""
        self.award = ""


def apply_label(state, line):
    """Update division/section/award state from a text line, if it is a label."""
    m = re.match(r"^【(.+)】$", line)
    if m:
        inner = m.group(1)
        if "部門" in inner:
            state.division = norm_division(inner)
        elif inner.startswith("UDC") and "賞" not in inner:
            state.division = "一般部門"
        else:
            state.division = norm_division(inner)
        state.section = inner
        state.award = ""
        return True
    if line in MEDALS:
        state.award = line
        return True
    if (
        "。" not in line and "、" not in line and len(line) <= 40
        and re.search(r"賞(（.*）)?$", line)
    ):
        name = re.sub(r"（.*）$", "", line)
        state.division = norm_division(name)
        state.section = name
        state.award = ""
        return True
    if re.match(r"^[^\s]{2,20}部門$", line) and "。" not in line:
        state.division = norm_division(line)
        state.section = line
        state.award = ""
        return True
    return False


def parse_table(year, table, state, url):
    """Turn one table into records. Returns (records, skipped_rows)."""
    records, skipped = [], 0
    if not table:
        return records, skipped
    if "地域拠点" in state.section:
        return records, len(table)  # regional-block awards are not works
    header = table[0]
    pseudo_header = header[0] == "" and set(header) <= {"", "作品名", "チーム名"}
    has_header = any(h in HEADER_WORDS for h in header) and not pseudo_header
    rows = table[1:] if (has_header or pseudo_header) else table
    colmap = {}
    if has_header:
        for i, h in enumerate(header):
            colmap[h] = i
        if "拠点名" in colmap:
            return records, len(rows)  # regional-block awards are not works

    for row in rows:
        if not row or all(not c for c in row):
            continue
        rec = {
            "year": year, "division": "", "award": "", "theme": "",
            "type": "", "title": "", "team": "", "source_url": url,
        }
        if has_header:
            def cell(name):
                i = colmap.get(name)
                return row[i] if i is not None and i < len(row) else ""

            rec["theme"] = cell("テーマ")
            rec["type"] = cell("タイプ")
            rec["title"] = cell("作品名")
            rec["team"] = cell("チーム名") or cell("代表者名／チーム名")
            rec["award"] = cell("賞") or state.award or state.section
            rec["division"] = (
                norm_division(cell("部門")) if cell("部門") else state.division
            )
        elif len(row) >= 3 and AWARD_RE.match(row[0]):
            rec["award"], rec["title"], rec["team"] = row[0], row[1], row[2]
            rec["division"] = state.division
        elif len(row) == 2:
            rec["title"], rec["team"] = row[0], row[1]
            rec["award"] = state.award or state.section
            rec["division"] = state.division
        elif len(row) >= 3:
            rec["title"], rec["team"] = row[0], row[1]
            rec["award"] = state.award or state.section
            rec["division"] = state.division
        else:
            skipped += 1
            continue
        rec["title"] = clean_title(rec["title"])
        rec["team"] = clean_team(rec["team"])
        if not rec["title"] or rec["title"] == "該当なし":
            skipped += 1
            continue
        # 2015-2018 department sections double as the work type.
        if not rec["type"] and TYPE_DEPT_RE.match(rec["division"]):
            rec["type"] = rec["division"].replace("部門", "")
        records.append(rec)
    return records, skipped


AWARD_LINE_RE = re.compile(r"^(金賞|銀賞|銅賞|最優秀賞|優秀賞|特別賞|奨励賞)「(.+)」$")
TITLE_ONLY_RE = re.compile(r"^「(.+)」$")
DEPT_TITLE_RE = re.compile(r"^([^\s「]{2,20}部門)\s*「(.+)」$")


def parse_text_year(year, segments, url):
    """Parse the narrative 2015/2016 pages."""
    records = []
    state = State()
    lines = []
    for kind, payload in segments:
        if kind == "text":
            lines.extend(payload)
    # Only work on the article body: start at the results heading.
    start = next(
        (i for i, l in enumerate(lines) if "審査結果" in l and str(year) in l),
        0,
    )
    lines = lines[start:]
    i = 0
    while i < len(lines):
        line = lines[i]
        if apply_label(state, line):
            i += 1
            continue
        m = AWARD_LINE_RE.match(line)
        if m:
            team = ""
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if (
                    "。" not in nxt and "「" not in nxt
                    and "プレゼンター" not in nxt and "受賞" not in nxt
                    and len(nxt) <= 60 and not AWARD_LINE_RE.match(nxt)
                    and not re.match(r"^【.*】$", nxt)
                ):
                    team = nxt
                    i += 1
            records.append({
                "year": year, "division": state.division,
                "award": m.group(1), "theme": "", "type": "",
                "title": clean_title(m.group(2)), "team": clean_team(team),
                "source_url": url,
            })
            i += 1
            continue
        m = DEPT_TITLE_RE.match(line)
        if m:
            records.append({
                "year": year, "division": norm_division(m.group(1)),
                "award": state.section or state.award, "theme": "",
                "type": "", "title": clean_title(m.group(2)), "team": "",
                "source_url": url,
            })
            i += 1
            continue
        m = TITLE_ONLY_RE.match(line)
        if m and ("賞" in state.section or "賞" in state.division):
            records.append({
                "year": year, "division": state.division,
                "award": state.section or state.award, "theme": "",
                "type": "", "title": clean_title(m.group(1)), "team": "",
                "source_url": url,
            })
            i += 1
            continue
        i += 1
    for rec in records:
        if not rec["type"] and TYPE_DEPT_RE.match(rec["division"]):
            rec["type"] = rec["division"].replace("部門", "")
    return records


def count_source_rows(year, html_text, segments):
    """Independent count of work rows present in the source page."""
    if year in TEXT_YEARS:
        txt = re.sub(
            r"<[^>]+>", "\n",
            re.sub(r"<script.*?</script>", "", html_text, flags=re.S),
        )
        lines = [norm(l) for l in txt.split("\n")]
        start = next(
            (i for i, l in enumerate(lines)
             if "審査結果" in l and str(year) in l),
            0,
        )
        lines = lines[start:]
        n = sum(1 for l in lines if AWARD_LINE_RE.match(l))
        n += sum(1 for l in lines if TITLE_ONLY_RE.match(l))
        n += sum(1 for l in lines if DEPT_TITLE_RE.match(l))
        return n
    total = 0
    state = State()
    for kind, payload in segments:
        if kind == "text":
            for line in payload:
                apply_label(state, line)
        else:
            table = payload
            if not table or "地域拠点" in state.section:
                continue
            header = table[0]
            has_header = any(h in HEADER_WORDS for h in header)
            if has_header and "拠点名" in header:
                continue
            rows = table[1:] if has_header else table
            for row in rows:
                joined = "".join(row)
                if joined and "該当なし" not in joined:
                    total += 1
    return total


def parse_page(year, html_text, url):
    segments = split_segments(html_text)
    if year in TEXT_YEARS:
        records = parse_text_year(year, segments, url)
        return records, count_source_rows(year, html_text, segments)
    state = State()
    records = []
    for kind, payload in segments:
        if kind == "text":
            for line in payload:
                apply_label(state, line)
        else:
            recs, _ = parse_table(year, payload, state, url)
            records.extend(recs)
    return records, count_source_rows(year, html_text, segments)


# ---------------------------------------------------------------------- main

def fetch(url, cache_path, no_fetch):
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="ignore")
    if no_fetch:
        raise FileNotFoundError(f"no cache for {url} and --no-fetch set")
    req = urllib.request.Request(
        url, headers={"User-Agent": "udc-awards-skill/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read().decode("utf-8", errors="ignore")
    cache_path.write_text(data, encoding="utf-8")
    time.sleep(1)  # be polite
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", default=str(Path.home() / "workspace/projects/udc-awards")
    )
    ap.add_argument(
        "--no-fetch", action="store_true", help="use cached raw HTML only"
    )
    args = ap.parse_args()
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    all_records, validation = [], {}
    for year in sorted(RESULT_URLS):
        url = RESULT_URLS[year]
        cache = raw / f"{year}.html"
        try:
            html_text = fetch(url, cache, args.no_fetch)
        except Exception as e:  # noqa: BLE001
            validation[str(year)] = {"source_url": url, "error": str(e)}
            print(f"{year}: FETCH FAILED {e}", file=sys.stderr)
            continue
        records, source_rows = parse_page(year, html_text, url)
        # dedupe within year on (division, award, title, team)
        seen, uniq = set(), []
        for r in records:
            key = (r["division"], r["award"], r["title"], r["team"])
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        validation[str(year)] = {
            "source_url": url,
            "source_work_rows": source_rows,
            "parsed_records": len(records),
            "after_dedupe": len(uniq),
        }
        print(
            f"{year}: source_rows={source_rows} "
            f"parsed={len(records)} deduped={len(uniq)}"
        )
        all_records.extend(uniq)

    fields = [
        "year", "division", "award", "theme", "type",
        "title", "team", "source_url",
    ]
    (out / "udc_awards.json").write_text(
        json.dumps(all_records, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with open(out / "udc_awards.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_records)
    (out / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"total records: {len(all_records)} -> {out}")


if __name__ == "__main__":
    main()
