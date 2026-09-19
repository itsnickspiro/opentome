#!/usr/bin/env python3
"""Generate the opentomedb.com site from the published catalogue.

    python3 site/build_site.py <manga-metadata.sqlite> <version.json> <out-dir>

Every number on the site is read from the artifact at build time; every document
is rendered from the repository's own Markdown (docs/, corrections/). Nothing is
hard-coded that the artifact or the docs can answer. Standard library only.

The changelog is the list of per-build releases on the mangarr-metadata repository
(tags `opentome-YYYY-MM-DD`). If the GitHub API cannot be reached the page is still
written, with a one-line notice -- a deploy never fails over the changelog.
"""
import html
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
REPO_DIR = SITE_DIR.parent
TEMPLATES = SITE_DIR / "templates"

DATA_REPO = "itsnickspiro/mangarr-metadata"
CODE_REPO = "itsnickspiro/opentome"
ARTIFACT_URL = f"https://github.com/{DATA_REPO}/releases/download/metadata/manga-metadata.sqlite"
MANIFEST_URL = f"https://github.com/{DATA_REPO}/releases/download/metadata/version.json"
RELEASES_URL = f"https://github.com/{DATA_REPO}/releases"
RELEASES_API = f"https://api.github.com/repos/{DATA_REPO}/releases"
CODE_URL = f"https://github.com/{CODE_REPO}"
ISSUES_URL = f"{CODE_URL}/issues/new/choose"
ISSUE_FORM = f"{CODE_URL}/issues/new?template={{}}"
BLOB_URL = f"{CODE_URL}/blob/main/{{}}"
TREE_URL = f"{CODE_URL}/tree/main/{{}}"
DISCORD_URL = "https://discord.gg/bQVwv54KdP"   # permanent invite; #corrections (forum) and #announcements

BUILD_TAG = re.compile(r"^opentome-\d{4}-\d{2}-\d{2}$")
NOTES_LINE = re.compile(r"OpenTome (opentome-\d{4}-\d{2}-\d{2}): (\d+) series, (\d+) volumes")

# Markets and media with fewer lines than this are grouped as "other" -- on the
# home counts table and as one browse page. The four markets the pitch names always
# get their own row and page, however few lines they have today (German: 35).
GROUP_MIN = 50
PITCHED_MARKETS = ("ja", "en", "fr", "de")

LANGUAGE_NAMES = {
    "ja": "Japanese", "en": "English", "fr": "French", "de": "German", "ko": "Korean",
    "zh": "Chinese", "zh-TW": "Chinese (Taiwan)", "zh-HK": "Chinese (Hong Kong)",
    "it": "Italian", "es": "Spanish", "pt": "Portuguese", "pt-BR": "Portuguese (Brazil)",
}

# The four issue forms under .github/ISSUE_TEMPLATE/, in CONTRIBUTING.md's order.
ISSUE_FORMS = [
    ("Wrong fact", "wrong-fact.yml", "a date, ISBN, page count, title or name in the catalogue is wrong"),
    ("Missing volume", "missing-volume.yml", "a volume exists in a market and the catalogue does not have it"),
    ("New release line", "new-line.yml", "a whole edition is missing — a market, publisher or edition with no line at all"),
    ("Merge lines", "merge-lines.yml", "two entries are one release line, or two works are one work"),
]

e = html.escape


def fmt(n):
    return f"{n:,}"


def market_name(code):
    return LANGUAGE_NAMES.get(code, code)


def medium_name(m):
    return (m or "").replace("_", " ") or "—"


def human_size(n):
    return f"{n / 1_000_000:.1f} MB"


# --------------------------------------------------------------------------- templates

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(name, **ctx):
    """Fill {{name}} placeholders in a template. Values are HTML already; the
    template is the only thing scanned, so braces inside values are inert."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")

    def sub(m):
        key = m.group(1)
        if key not in ctx:
            raise KeyError(f"{name}: no value for {{{{{key}}}}}")
        return str(ctx[key])

    return _PLACEHOLDER.sub(sub, text)


def page(out_dir, rel_path, template, title, nav, build_label, wide=False, **ctx):
    """Render one page inside the base layout and write it. Links are relative to
    the page, so `root` is the prefix back to the site root ("" or "../")."""
    depth = rel_path.count("/")
    root = "../" * depth
    body = render(template, root=root, build_label=e(build_label), **ctx)
    doc = render("base.html", root=root, home=root or "./", title=e(title), body=body,
                 main_class=' class="wide"' if wide else "",
                 build_label=e(build_label), code_url=CODE_URL, discord_url=DISCORD_URL,
                 **{f"cur_{n}": (' aria-current="page"' if n == nav else "")
                    for n in ("home", "data", "contribute", "changelog", "browse")})
    path = out_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return rel_path, path.stat().st_size


# --------------------------------------------------------------------------- markdown

_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_TABLE_SEP = re.compile(r"^\|?[\s:\-|]+\|?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")


def inline(text, link_base=""):
    """Escape, then inline code, bold, italic, links -- in that order, with code
    spans protected so their content is never restyled."""
    spans = []

    def stash(m):
        spans.append(f"<code>{e(m.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE_SPAN.sub(stash, text)
    text = e(text, quote=False)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)

    def link(m):
        href = html.unescape(m.group(2))
        if link_base and not re.match(r"^(https?:)?//|^#", href):
            href = link_base + href
        return f'<a href="{e(href)}">{m.group(1)}</a>'

    text = _LINK.sub(link, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def markdown(text, heading_shift=1, link_base=""):
    """A small converter for the repository's own docs: headings, paragraphs,
    fenced code, bullet and numbered lists (with wrapped continuation lines),
    pipe tables, and the inline forms above. Nothing else is needed by them."""
    out = []
    para, items, table, code = [], [], [], None
    list_tag = None

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para), link_base)}</p>")
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li>{inline(i, link_base)}</li>" for i in items) + f"</{list_tag}>")
            items.clear()
        list_tag = None

    def flush_table():
        if table:
            head, *rows = table
            out.append("<div class=\"table-wrap\"><table><thead><tr>"
                       + "".join(f"<th>{inline(c, link_base)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c, link_base)}</td>" for c in r) + "</tr>" for r in rows)
                       + "</tbody></table></div>")
            table.clear()

    def flush():
        flush_para(); flush_list(); flush_table()

    for line in text.splitlines():
        if code is not None:
            if line.startswith("```"):
                out.append(f"<pre><code>{e(chr(10).join(code))}</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        if line.startswith("```"):
            flush()
            code = []
            continue
        if not line.strip():
            flush()
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            level = min(len(m.group(1)) + heading_shift, 6)
            out.append(f"<h{level}>{inline(m.group(2), link_base)}</h{level}>")
            continue
        if line.startswith("|"):
            flush_para(); flush_list()
            if _TABLE_SEP.match(line):
                continue
            table.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        flush_table()
        m = _ITEM.match(line)
        if m and m.group(1) == "":
            flush_para()
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            items.append(m.group(3))
            continue
        if items and line[0] == " ":      # a wrapped continuation of the item above
            items[-1] += " " + line.strip()
            continue
        flush_list()
        para.append(line.strip())
    flush()
    return "\n".join(out)


def doc_html(rel, heading_shift=1, start_at=None, end_at=None):
    """Render a repository Markdown file. Relative links inside it point at the
    file on GitHub. `start_at` drops everything before that heading line and
    `end_at` everything from that one on; without `start_at` the document's own
    title line is dropped (the page names the section)."""
    text = (REPO_DIR / rel).read_text(encoding="utf-8")
    if start_at:
        text = text[text.index(start_at):]
    elif text.startswith("# "):
        text = text.split("\n", 1)[1]
    if end_at:
        text = text[:text.index(end_at)]
    base = BLOB_URL.format(str(Path(rel).parent) + "/") if "/" in rel else BLOB_URL.format("")
    return markdown(text, heading_shift=heading_shift, link_base=base)


# --------------------------------------------------------------------------- data

def load(db_path, manifest_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    meta = {k: v for k, v in con.execute("SELECT key, value FROM meta")}
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    missing = [k for k in ("gcd_dump", "sha256", "size") if not manifest.get(k)]
    if missing:
        sys.exit(f"{manifest_path}: missing {', '.join(missing)} -- a broken manifest, not a site to build")
    manifest["size"] = int(manifest["size"])
    lines = con.execute(
        "SELECT name, language, medium, publisher, status, volume_count, dated_count,"
        " anilist_id, tome_id, tome_work_id FROM series ORDER BY name COLLATE NOCASE, tome_id"
    ).fetchall()
    volumes, dated = con.execute(
        "SELECT count(*), count(release_date) FROM volumes").fetchone()
    tables = []
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cols = [r["name"] for r in con.execute(f"PRAGMA table_info({name})")]
        rows = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        filled = con.execute(
            "SELECT " + ", ".join(f"count({c})" for c in cols) + f" FROM {name}").fetchone()
        empty = {c for c, n in zip(cols, filled) if rows and n == 0}
        tables.append((name, rows, cols, empty))
    con.close()
    return meta, manifest, lines, volumes, dated, tables


def group_markets(lines):
    """Market code -> its lines, with markets under GROUP_MIN (and not pitched)
    folded into 'other'. Ordered by size; 'other' last."""
    by_market = defaultdict(list)
    for r in lines:
        by_market[r["language"] or ""].append(r)
    big = sorted((m for m in by_market if len(by_market[m]) >= GROUP_MIN or m in PITCHED_MARKETS),
                 key=lambda m: -len(by_market[m]))
    small = [m for m in by_market if m not in big]
    groups = [(m, by_market[m]) for m in big]
    if small:   # keep the artifact's name order across the grouped markets
        groups.append(("other", [r for r in lines if r["language"] in small]))
    return groups, small


def releases(build_label):
    """Per-build releases, newest first, from the GitHub API; None if unreachable."""
    entries = []
    try:
        page_no = 1
        while True:
            req = urllib.request.Request(f"{RELEASES_API}?per_page=100&page={page_no}",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "opentomedb.com site build"})
            token = os.environ.get("GH_TOKEN")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=20) as resp:
                batch = json.loads(resp.read().decode("utf-8"))
            for rel in batch:
                tag = rel.get("tag_name", "")
                if not BUILD_TAG.match(tag):
                    continue
                m = NOTES_LINE.search(rel.get("body") or "")
                entries.append({
                    "tag": tag,
                    "published_at": rel.get("published_at") or "",
                    "series": int(m.group(2)) if m else None,
                    "volumes": int(m.group(3)) if m else None,
                    "url": rel.get("html_url") or f"{RELEASES_URL}/tag/{tag}",
                    "current": tag == build_label,
                })
            if len(batch) < 100:
                break
            page_no += 1
    except Exception as exc:  # network, rate limit, bad JSON -- the page still renders
        print(f"changelog: releases unavailable ({exc.__class__.__name__}: {exc})", file=sys.stderr)
        return None
    entries.sort(key=lambda r: r["tag"], reverse=True)
    return entries


# --------------------------------------------------------------------------- pages

def counts_table(lines, groups):
    """Release lines by market (rows) and medium (columns), small ones as 'other'."""
    media_total = Counter(r["medium"] or "" for r in lines)
    big_media = [m for m, _ in media_total.most_common() if media_total[m] >= GROUP_MIN]
    has_other = any(m not in big_media for m in media_total)
    cols = big_media + (["other"] if has_other else [])
    head = "<tr><th>market</th>" + "".join(f"<th>{e(medium_name(c))}</th>" for c in cols) + "<th>total</th></tr>"
    body, col_totals = [], Counter()
    for code, rows in groups:
        c = Counter((r["medium"] if r["medium"] in big_media else "other") for r in rows)
        col_totals.update(c)
        label = market_name(code) if code != "other" else "other markets"
        body.append(f"<tr><th>{e(label)}</th>"
                    + "".join(f"<td>{fmt(c[k]) if c[k] else '·'}</td>" for k in cols)
                    + f"<td>{fmt(len(rows))}</td></tr>")
    foot = ("<tr><th>total</th>" + "".join(f"<td>{fmt(col_totals[k])}</td>" for k in cols)
            + f"<td>{fmt(len(lines))}</td></tr>")
    return (f'<div class="table-wrap"><table class="counts"><thead>{head}</thead>'
            f'<tbody>{"".join(body)}</tbody><tfoot>{foot}</tfoot></table></div>')


def browse_rows(rows, show_market):
    out = []
    for r in rows:
        anilist = (f'<a href="https://anilist.co/manga/{r["anilist_id"]}">{r["anilist_id"]}</a>'
                   if r["anilist_id"] else "")
        market = f"<td>{e(market_name(r['language']))}</td>" if show_market else ""
        out.append(
            f"<tr><td>{e(r['name'])}</td>{market}<td>{e(medium_name(r['medium']))}</td>"
            f"<td>{r['volume_count']}</td><td>{r['dated_count']}</td>"
            f"<td>{e(r['publisher'] or '')}</td><td>{e(r['status'] or '')}</td>"
            f"<td>{e(r['tome_id'] or '')}</td><td>{anilist}</td></tr>")
    return "\n".join(out)


def changelog_html(entries):
    if entries is None:
        return (f'<p class="notice">The release list could not be fetched when this page was built. '
                f'Every build is listed on <a href="{RELEASES_URL}">GitHub</a>.</p>')
    if not entries:
        return f'<p class="notice">No build has been published yet. Builds appear on <a href="{RELEASES_URL}">GitHub</a> first.</p>'
    items = []
    for i, r in enumerate(entries):
        date = r["published_at"][:10]
        counts = ("" if r["series"] is None else
                  f"{fmt(r['series'])} series lines · {fmt(r['volumes'])} volumes")
        prev = entries[i + 1] if i + 1 < len(entries) else None
        delta = ""
        if prev and r["series"] is not None and prev["series"] is not None:
            ds, dv = r["series"] - prev["series"], r["volumes"] - prev["volumes"]
            delta = f'<span class="delta">{ds:+,} lines · {dv:+,} volumes since {e(prev["tag"])}</span>'
        badge = '<span class="badge">current</span>' if r["current"] else ""
        items.append(
            f'<article class="release"><h2><a href="{e(r["url"])}">{e(r["tag"])}</a>{badge}</h2>'
            f'<p class="meta"><time datetime="{e(r["published_at"])}">{e(date)}</time>'
            f'{" · " + counts if counts else ""}</p>{delta}</article>')
    return "\n".join(items)


def tables_html(tables):
    def col(c, empty):
        return f"<code>{e(c)}</code>" + (' <span class="empty">(empty in this build)</span>' if c in empty else "")
    rows = "".join(
        f"<tr><td><code>{e(name)}</code></td><td>{fmt(n)}</td>"
        f"<td>{', '.join(col(c, empty) for c in cols)}</td></tr>"
        for name, n, cols, empty in tables)
    return (f'<div class="table-wrap"><table class="schema"><thead><tr><th>table</th><th>rows</th>'
            f'<th>columns</th></tr></thead><tbody>{rows}</tbody></table></div>')


def meta_html(meta):
    rows = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(v or '')}</td></tr>"
                   for k, v in sorted(meta.items()))
    return (f'<div class="table-wrap"><table class="kv"><thead><tr><th>key</th><th>value</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>')


def build(db_path, manifest_path, out_dir):
    meta, manifest, lines, volumes, dated, tables = load(db_path, manifest_path)
    build_label = meta.get("gcd_dump", "")
    if build_label != manifest.get("gcd_dump"):
        sys.exit(f"artifact label {build_label!r} != manifest label {manifest.get('gcd_dump')!r}")
    groups, small_markets = group_markets(lines)
    generated = meta.get("generated_at", "")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "static").mkdir(exist_ok=True)
    written = []

    for asset in ("style.css",):
        data = (SITE_DIR / "static" / asset).read_bytes()
        (out_dir / "static" / asset).write_bytes(data)
        written.append((f"static/{asset}", len(data)))
    cname = (SITE_DIR / "CNAME").read_bytes()
    (out_dir / "CNAME").write_bytes(cname)
    written.append(("CNAME", len(cname)))

    common = dict(build_label=build_label, artifact_url=ARTIFACT_URL, manifest_url=MANIFEST_URL,
                  releases_url=RELEASES_URL, code_url=CODE_URL)

    # home
    written.append(page(
        out_dir, "index.html", "home.html", "OpenTomeDB — the open manga and light-novel volume database",
        "home", sha256=e(manifest["sha256"]), size=e(human_size(manifest["size"])),
        size_bytes=fmt(manifest["size"]), generated=e(generated[:10]),
        counts_table=counts_table(lines, groups), n_lines=fmt(len(lines)),
        n_works=fmt(len({r["tome_work_id"] for r in lines})),
        n_volumes=fmt(volumes), n_dated=fmt(dated),
        pct_dated=f"{100 * dated / volumes:.1f}" if volumes else "0",
        n_markets=str(len({r["language"] for r in lines})),
        **common))

    # data
    written.append(page(
        out_dir, "data/index.html", "data.html", "OpenTomeDB — Data", "data",
        tables=tables_html(tables), meta_table=meta_html(meta),
        attribution=e(meta.get("attribution", "")), licence_text=e(meta.get("licence", "")),
        id_scheme=doc_html("docs/id-scheme.md"), schema_notes=doc_html("docs/schema-v1.md"),
        licence_data_url=BLOB_URL.format("LICENSE-DATA.md"),
        legal_url=BLOB_URL.format("docs/legal-position.md"),
        license_url=BLOB_URL.format("LICENSE"),
        **common))

    # contribute
    forms = "".join(
        f'<tr><td><a href="{e(ISSUE_FORM.format(f))}">{e(name)}</a></td><td>{e(when)}</td></tr>'
        for name, f, when in ISSUE_FORMS)
    written.append(page(
        out_dir, "contribute/index.html", "contribute.html", "OpenTomeDB — Contribute", "contribute",
        issue_forms=forms, issues_url=ISSUES_URL, discord_url=DISCORD_URL, wrong_fact_url=ISSUE_FORM.format("wrong-fact.yml"),
        pr_template_url=BLOB_URL.format(".github/PULL_REQUEST_TEMPLATE.md"),
        contributing_url=BLOB_URL.format("CONTRIBUTING.md"),
        corrections_url=TREE_URL.format("corrections"),
        corrections_readme_url=BLOB_URL.format("corrections/README.md"),
        legal_url=BLOB_URL.format("docs/legal-position.md"),
        licence_data_url=BLOB_URL.format("LICENSE-DATA.md"),
        correction_files=doc_html("corrections/README.md", heading_shift=1, start_at="## Files"),
        how_built=doc_html("README.md", heading_shift=0, start_at="## How the catalogue is built", end_at="## Docs"),
        readme_url=BLOB_URL.format("README.md"),
        **common))

    # changelog
    entries = releases(build_label)
    written.append(page(
        out_dir, "changelog/index.html", "changelog.html", "OpenTomeDB — Changelog", "changelog",
        entries=changelog_html(entries), **common))

    # browse
    index_rows = []
    for code, rows in groups:
        fname = f"{code}.html"
        label = market_name(code) if code != "other" else "Other markets"
        n_vol = sum(r["volume_count"] for r in rows)
        n_dated = sum(r["dated_count"] for r in rows)
        sub = ""
        if code == "other":
            sub = " (" + ", ".join(f"{market_name(m)} {fmt(sum(1 for r in rows if r['language'] == m))}"
                                   for m in sorted(small_markets, key=lambda m: -sum(1 for r in rows if r["language"] == m))) + ")"
        index_rows.append(
            f'<tr><td><a href="{e(fname)}">{e(label)}</a>{e(sub)}</td><td>{fmt(len(rows))}</td>'
            f'<td>{fmt(n_vol)}</td><td>{fmt(n_dated)}</td></tr>')
        media = Counter(r["medium"] or "" for r in rows)
        media_line = ", ".join(f"{fmt(n)} {medium_name(m)}" for m, n in media.most_common())
        written.append(page(
            out_dir, f"browse/{fname}", "browse.html", f"OpenTomeDB — Browse {label}", "browse",
            wide=True, market=e(label), n_lines=fmt(len(rows)), media_line=e(media_line),
            market_head="<th><button type=\"button\">Market</button></th>" if code == "other" else "",
            rows=browse_rows(rows, show_market=(code == "other")),
            **common))
    written.append(page(
        out_dir, "browse/index.html", "browse_index.html", "OpenTomeDB — Browse", "browse",
        market_rows="".join(index_rows), n_lines=fmt(len(lines)), n_markets=str(len(groups)),
        **common))

    for rel, size in written:
        print(f"{size:>10,}  {rel}")
    print(f"{len(written)} files → {out_dir}  ({build_label})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    build(*sys.argv[1:])
