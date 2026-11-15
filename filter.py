#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merge the 49 journal feeds in feeds.json, filter by keywords, and output
feed.xml (AI) and feed-ai-employee.xml (AI x Employee). Stdlib only.

To change keywords: edit AI_RE / EMP_RE in the CONFIG block below and push.
"""

import json
import re
import sys
import time
import email.utils
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

# ---------------- CONFIG ----------------
AI_RE = (
    r'artificial intelligen|\bAI\b|\bAGI\b|generative|large language model|'
    r'\bLLMs?\b|chatgpt|\bGPT|machine learning|deep learning|algorithm|'
    r'robot|automation|automat(ed|e|ion)|chatbot'
)
EMP_RE = (
    r'employ|worker|workforce|human resource|personnel|staff|supervis|'
    r'manag|leader|workplace|\bjob|occupation|organi'
)
MAX_ITEMS = 500          # max items per output feed
RSS_DAYS = 120           # keep items newer than N days
OPENALEX_DAYS = 90       # OpenAlex fallback: fetch works from last N days
OPENALEX_FROM = None     # advanced: fixed start date "YYYY-MM-DD" (None = use OPENALEX_DAYS)
MAILTO = "user@example.com"   # OpenAlex politeness email; change to yours
TIMEOUT = 40
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# ----------------------------------------

HERE = Path(__file__).resolve().parent
ATOM = "http://www.w3.org/2005/Atom"


def fetch(url):
    last = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return email.utils.parsedate_to_datetime(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def clean(html):
    return re.sub(r"<[^>]+>", " ", html or "")


def parse_feed(xml_bytes):
    """Parse RSS2 or Atom, return [{title, link, date, desc}]"""
    root = ET.fromstring(xml_bytes)
    items = []

    for it in root.findall(".//item"):
        title = clean(it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        date = parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        desc = clean(it.findtext("description") or "")
        items.append({"title": title, "link": link, "date": date, "desc": desc})

    for it in root.findall(".//{%s}entry" % ATOM):
        title = clean(it.findtext("{%s}title" % ATOM) or "").strip()
        link = ""
        for l in it.findall("{%s}link" % ATOM):
            link = l.get("href") or ""
            if link and l.get("rel") in (None, "alternate"):
                break
        date = parse_date(it.findtext("{%s}published" % ATOM) or it.findtext("{%s}updated" % ATOM))
        desc = clean(it.findtext("{%s}summary" % ATOM) or it.findtext("{%s}content" % ATOM) or "")
        items.append({"title": title, "link": link, "date": date, "desc": desc})
    return items


def oa_abstract(inv):
    if not inv:
        return ""
    pos = []
    for w, idxs in inv.items():
        pos.extend((i, w) for i in idxs)
    return " ".join(w for _, w in sorted(pos))


def openalex_items(issn):
    since = OPENALEX_FROM or (datetime.now(timezone.utc) - timedelta(days=OPENALEX_DAYS)).date().isoformat()
    f = "primary_location.source.issn:%s,from_publication_date:%s" % (issn, since)
    url = ("https://api.openalex.org/works?filter=" + urllib.parse.quote(f, safe=":,")
           + "&sort=publication_date:desc&per-page=100&mailto=" + urllib.parse.quote(MAILTO))
    out = []
    for attempt in range(2):
        try:
            d = json.loads(fetch(url))
            for w in d.get("results", []):
                t = w.get("title") or w.get("display_name") or ""
                link = (w.get("doi") or "").replace("http://", "https://")
                date = parse_date(w.get("publication_date") or "")
                out.append({"title": clean(t).strip(), "link": link, "date": date,
                            "desc": oa_abstract(w.get("abstract_inverted_index"))})
            return out
        except Exception:
            time.sleep(3)
    return out


def main():
    cfg = json.loads((HERE / "feeds.json").read_text(encoding="utf-8"))
    journals = cfg["journals"]
    ai_rx = re.compile(AI_RE, re.I)
    emp_rx = re.compile(EMP_RE, re.I)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RSS_DAYS)
    seen, ai_items, ai_emp_items = set(), [], []

    for j in journals:
        raw = None
        src = "rss"
        try:
            raw = parse_feed(fetch(j["rss"]))
        except Exception:
            raw = []
        if not raw:
            raw = openalex_items(j["issn"])
            src = "openalex" if raw else "none"

        got_ai = got_both = 0
        for it in raw:
            key = (it["link"] or it["title"]).lower()
            if key.startswith("http"):
                key = re.sub(r"^https?://doi\.org/", "", key)
            if key in seen:
                continue
            seen.add(key)
            d = it["date"]
            if d is None:
                d = datetime.now(timezone.utc)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d < cutoff:
                continue
            text = it["title"] + " \n " + it["desc"]
            if ai_rx.search(text):
                got_ai += 1
                ai_items.append((d, j, it))
                if emp_rx.search(text):
                    got_both += 1
                    ai_emp_items.append((d, j, it))
        print("%02d %-8s src=%-8s items=%4d AI=%3d AIxEMP=%3d" % (j["n"], j["short"], src, len(raw), got_ai, got_both), flush=True)
        time.sleep(0.4)

    write_feed("feed.xml", ai_items, "OB/Management/Psychology - AI tracked (49 journals)")
    write_feed("feed-ai-employee.xml", ai_emp_items, "OB/Management/Psychology - AI x Employee (49 journals)")


def write_feed(path, entries, title):
    entries.sort(key=lambda x: x[0], reverse=True)
    entries = entries[:MAX_ITEMS]
    L = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0">', "<channel>",
         "<title>%s</title>" % escape(title),
         "<link>https://github.com/</link>",
         "<description>%s</description>" % escape(title),
         "<lastBuildDate>%s</lastBuildDate>" % email.utils.format_datetime(datetime.now(timezone.utc))]
    for d, j, it in entries:
        desc = it["desc"][:3000].replace("]]>", "]]>")
        L += ["<item>",
              "<title><![CDATA[[%s] %s]]></title>" % (j["short"], it["title"]),
              "<link>%s</link>" % escape(it["link"]),
              '<guid isPermaLink="false">%s</guid>' % escape(it["link"] or it["title"]),
              "<pubDate>%s</pubDate>" % email.utils.format_datetime(d),
              "<category>%s</category>" % escape(j["name"]),
              "<description><![CDATA[%s]]></description>" % desc,
              "</item>"]
    L += ["</channel>", "</rss>"]
    (HERE / path).write_text("\n".join(L), encoding="utf-8")
    print("-> %s: %d items" % (path, len(entries)), file=sys.stderr)


if __name__ == "__main__":
    main()
