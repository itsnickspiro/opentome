"""Facts from each work's MAIN Wikipedia article: status, dates, publishers.

The catalogue is built from LIST articles, which carry the volumes but say
nothing about the work as a whole. The main article's `{{Infobox animanga/Print}}`
does, in machine-readable fields rather than prose:

    | first        = September 8, 2011      -> the run started
    | last         = September 18, 2014     -> the run ENDED  (absent while ongoing)
    | volumes      = 14
    | publisher    = [[Shueisha]]
    | publisher_en = {{English manga publisher|NA=Viz Media|UK=...}}
    | demographic  = [[Seinen manga|Seinen]]
    | magazine     = [[Weekly Young Jump]]

Why this matters: the artifact shipped with `status` null for every line, so
Mangarr fell back to AniList for finished-vs-ongoing. That is acceptable but it
is a second opinion where the source itself states the fact. `last` being
present is exactly the "finished" signal; its absence while `first` is present
is "ongoing". Both are dated facts, not prose.

One request per work (4,342), cached forever like everything else. Written as
claims (source `wikipedia`, facts only) and onto `work` / `release_line` so the
export can read them without a join.
"""
import datetime, json, os, re, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "schema"))
from wikipedia_volumes import wikitext, parse_date, _clean, EN_MONTHS
from main_titles import main_article, _norm
from load import LICENCE

NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

INFOBOX = re.compile(r"\{\{\s*Infobox animanga/(Print|Manga|Novel)\b", re.I)
HEADER = re.compile(r"\{\{\s*Infobox animanga/Header\b", re.I)


def _block(w, start):
    """The balanced {{...}} starting at `start`."""
    i, depth = start, 0
    while i < len(w):
        if w.startswith("{{", i):
            depth += 1; i += 2; continue
        if w.startswith("}}", i):
            depth -= 1; i += 2
            if depth == 0:
                return w[start:i]
            continue
        i += 1
    return w[start:]


def _fields(block):
    """Top-level |key = value pairs of one template (nested templates kept whole)."""
    out, depth, buf = {}, 0, []
    body = block[2:-2]
    parts = []
    for ch in body:
        if ch == "{" or ch == "[":
            depth += 1
        elif ch == "}" or ch == "]":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            out[k.strip().lower()] = v.strip()
    return out


def _unlink(v):
    """[[Kodansha USA|Kodansha Comics]] -> Kodansha Comics; [[X]] -> X. Done BEFORE
    splitting a template on '|', or the link's own pipe shatters the field."""
    v = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", v or "")
    return re.sub(r"\[\[([^\]]*)\]\]", r"\1", v)


LIST_TPL = re.compile(r"^\s*\{\{\s*(?:ubl|unbulleted list|plain ?list|hlist|flatlist)\s*\|(.*)\}\}\s*$",
                      re.I | re.S)


def _split_top(body):
    """Split on '|' at depth 0 only -- nested templates and links stay whole."""
    parts, buf, depth = [], [], 0
    for ch in body:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _first_of_list(v):
    """{{ubl|Kodansha|Kodansha USA}} / {{plainlist|* A\n* B}} -> the FIRST entry. An
    infobox that lists several publishers leads with the original one."""
    m = LIST_TPL.match(v or "")
    if not m:
        return v
    items = []
    for part in _split_top(m.group(1)):
        # bullets: "\n* A\n* B" in wikitext, "* A * B" once whitespace is collapsed
        for x in re.split(r"(?:^|\s)\*\s*", part):
            x = x.strip(" *\n\t")
            if x and not re.match(r"^[a-z_ ]+=", x, re.I):
                items.append(x)
    return items[0] if items else ""


# Status words an infobox hangs on a publisher; a bare status is dropped from the value.
_PUB_STATUS = re.compile(r"\(\s*(former(ly)?|current(ly)?|expired|revoked|defunct|original)\s*\)", re.I)
# A publisher that is no longer the publisher: skipped when a later entry is not.
_PUB_STALE = re.compile(r"\b(former(ly)?|expired|revoked|defunct|original creator|webcomic|self-published|1st edition)\b", re.I)
_PUB_NOW = re.compile(r"\b(current|present|print)\b", re.I)


def _publisher_field(v):
    """One publisher from a field that may list several with <br> and annotate them
    with <small>: 'Tokyopop (former)<br />J-Novel Club' -> 'J-Novel Club';
    'Kadokawa Shoten <small>(vol. 1-2)</small><br />Media Factory <small>(vol. 3-present)</small>'
    -> 'Media Factory (vol. 3-present)'. A single entry keeps its annotation minus a bare
    status word ('Tokyopop <small>(former)</small>' -> 'Tokyopop'). The 113 lines the first
    public artifact shipped with '<br>' inside the publisher came from here (2026-09-19)."""
    v = _first_of_list(v or "")
    v = re.sub(r"<small>\s*(.*?)\s*</small>", r" \1 ", v, flags=re.S | re.I)
    entries = []          # (value, stale, now) -- judged on the raw entry, before its status word goes
    for part in re.split(r"<br\s*/?>", v, flags=re.I):
        part = _clean(_unlink(re.sub(r"<[^>]+>", "", part)))
        part = part.replace("{{", "").replace("}}", "")
        stale, now = bool(_PUB_STALE.search(part)), bool(_PUB_NOW.search(part))
        part = re.sub(r"\s+", " ", _PUB_STATUS.sub("", part)).strip(" ,;")
        if part and not (part.startswith("(") and part.endswith(")")):
            entries.append((part, stale, now))
        elif part and entries:
            value, st, nw = entries[-1]
            entries[-1] = (f"{value} {part}", st, nw)
    if not entries:
        return ""
    live = [e for e in entries if not e[1]]
    now = [e for e in live if e[2]]
    return (now or live or entries)[0][0]


REGION = {"NA", "US", "EN", "UK", "AUS", "NZ", "SEA", "CAN", "CA", "IN", "PH", "SG", "MY"}


def _publisher_en(v):
    """{{English manga publisher|NA=Viz Media|UK=...}} -> 'Viz Media' (NA first).

    Also the plural template, values that are lists ({{ubl|...}}), and the
    malformed-but-common positional form One Piece uses:
    `| [[Northern America|NA]]/[[United Kingdom|UK]] | [[Viz Media]]`.
    """
    v = _first_of_list(v)
    m = re.search(r"\{\{\s*English manga publishers?\s*\|(.*)\}\}", v or "", re.S | re.I)
    if not m:
        return _publisher_field(v) or None
    fields, positional = {}, []
    for part in _split_top(m.group(1)):
        if "=" in part:
            k, _, val = part.partition("=")
            fields[_clean(_unlink(k)).upper()] = _publisher_field(val)
        else:
            positional.append(_publisher_field(part))
    for region in ("NA", "US", "EN", "UK"):
        if fields.get(region):
            return fields[region]
    for p in positional:          # skip region labels like 'NA/UK'
        if p and not all(t.strip().upper() in REGION for t in re.split(r"[/,&]", p)):
            return p
    for region in ("AUS", "NZ", "SEA"):
        if fields.get(region):
            return fields[region]
    return next((x for x in fields.values() if x), None)


def _list_items(v):
    """Every entry of a list-valued field: a {{ubl}}/{{plainlist}} template, or
    values separated by <br>, commas or slashes. Links unwrapped, refs stripped."""
    v = re.sub(r"<!--.*?-->", "", v or "", flags=re.S)
    m = LIST_TPL.match(v.strip())
    if m:
        raw = []
        for part in _split_top(m.group(1)):
            for x in re.split(r"(?:^|\s)\*\s*", part):
                x = x.strip(" *\n\t")
                if x and not re.match(r"^[a-z_ ]+=", x, re.I):
                    raw.append(x)
    else:
        # refs, templates and links go FIRST: a <ref>{{cite web|url=https://…/a/b}}</ref>
        # split on '/' and '|' before it was stripped shipped six URL fragments as genres
        v = _clean(_unlink(re.sub(r"<br\s*/?>", "|", v, flags=re.I)))
        raw = re.split(r"\s*(?:\||,|/|;|\band\b)\s*", v)
    out = []
    for x in raw:
        x = _clean(_unlink(x)).strip(" ,;")
        if x and x.lower() not in ("and", "&") and x not in out:
            out.append(x)
    return out


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")


def _print_blocks(w):
    return [_fields(_block(w, m.start())) for m in INFOBOX.finditer(w or "")]


def parse_main(w, list_article=None):
    """-> dict of facts from the print infobox that describes OUR list article
    (its `volume_list` names it), else the first one, plus the header's title,
    genres and image. Sword Art Online's first print infobox is the 2002 web
    novel (ended 2008); the light-novel one, ongoing, is the third."""
    blocks = _print_blocks(w)
    if not blocks:
        return {}
    f = blocks[0]
    if list_article:
        want = _norm(list_article)
        for b in blocks:
            if _norm(_clean(_unlink(b.get("volume_list", "")))) == want:
                f = b
                break
    out = {}
    h = HEADER.search(w)
    if h:
        hf = _fields(_block(w, h.start()))
        nm = _clean(_unlink(hf.get("name", "")))
        if nm:
            out["name"] = nm
        for key in ("ja_kanji", "ja_romaji"):
            val = _clean(_unlink(hf.get(key, "")))
            if val:
                out[key] = val
        genres = [x for x in _list_items(hf.get("genre", "")) if 1 < len(x) <= 40][:6]
        if genres:
            out["genre"] = genres
        img = _unlink(hf.get("image", "")).strip()
        img = re.sub(r"^(?:File|Image):", "", img, flags=re.I).strip().split("|")[0].strip()
        if img.lower().endswith(IMAGE_EXT):
            out["image"] = img
    for key in ("author", "illustrator"):
        people = [x for x in _list_items(f.get(key, "")) if len(x) <= 60][:4]
        if people:
            out[key] = people
    imprint = _clean(_unlink(_first_of_list(f.get("imprint", ""))))
    if imprint:
        out["imprint"] = imprint
    first, _ = parse_date(f.get("first", ""), EN_MONTHS) if f.get("first") else (None, None)
    last, _ = parse_date(f.get("last", ""), EN_MONTHS) if f.get("last") else (None, None)
    if first:
        out["first"] = first
    if last:
        out["last"] = last
    if first or last:
        out["status"] = "ended" if last else "ongoing"
    vl = _clean(_unlink(f.get("volume_list", "")))
    if vl:
        out["volume_list"] = vl          # the list article this infobox points back to
    vols = _clean(f.get("volumes", ""))
    mv = re.match(r"(\d{1,4})", vols)
    if mv:
        out["volumes"] = int(mv.group(1))
    pub = _publisher_field(f.get("publisher", ""))
    if pub:
        out["publisher"] = pub
    pub_en = _publisher_en(f.get("publisher_en"))
    if pub_en:
        out["publisher_en"] = pub_en
    demo = _clean(f.get("demographic", ""))
    if demo:
        out["demographic"] = demo.split(",")[0].strip()
    mag = _clean(_unlink(_first_of_list(f.get("magazine", ""))))
    if mag:
        out["magazine"] = mag
    return out


def _names_back(facts, work_title, list_article):
    """A title-guessed page counts only if its infobox names the work back: the
    header's `name` carries the work's title, or `volume_list` is the very list
    article we came from (One Piece's header has no name; its `volume_list` is
    "List of One Piece manga volumes")."""
    if facts.get("name") and _norm(work_title) in _norm(facts["name"]):
        return True
    return bool(facts.get("volume_list")) and _norm(facts["volume_list"]) == _norm(list_article)


def run(dbpath, limit=None, verbose=True):
    db = sqlite3.connect(dbpath, timeout=60)
    c = db.cursor()
    works = c.execute("""SELECT t.work_id, t.title, w.primary_title FROM work_title t
                         JOIN work w ON w.id=t.work_id
                         WHERE t.language='en' AND t.kind='official'""").fetchall()
    if limit:
        works = works[:limit]
    done = {r[0] for r in c.execute("SELECT key FROM meta WHERE key LIKE 'main:%'")}
    t0, n_ok, n_status, n_pub, n_err = time.time(), 0, 0, 0, 0
    for i, (wid, art, prim) in enumerate(works, 1):
        target, _ = main_article(art, work_title=prim)
        via = "lead"
        if not target:
            # no italic link to the work in the lead (One Piece's volume list has
            # no prose at all): try the article named exactly after the work and
            # keep it only if its infobox header names the work back. The alias
            # stage never does this -- a guessed page's redirects are not aliases.
            target, via = prim, "title"
        if ("main:" + wid) in done:
            continue
        try:
            w = wikitext(target, "en")
            facts = parse_main(w, list_article=art)
            if via == "title" and not _names_back(facts, prim, art):
                facts = {}
            src = "https://en.wikipedia.org/wiki/" + target.replace(" ", "_")
            if facts.get("status"):
                c.execute("UPDATE work SET status=?, year_started=COALESCE(year_started, ?) WHERE id=?",
                          (facts["status"], int(facts["first"][:4]) if facts.get("first") else None, wid))
                for field in ("status", "first", "last", "volumes"):
                    if facts.get(field) is not None:
                        c.execute("""INSERT OR REPLACE INTO claim
                            (entity,entity_id,field,value,source,source_url,licence,retrieved_at)
                            VALUES('work',?,?,?,'wikipedia',?,?,?)""",
                            (wid, field, str(facts[field]), src, LICENCE["wikipedia"], NOW))
                n_status += 1
            if facts.get("demographic"):
                c.execute("UPDATE work SET demographic=COALESCE(demographic, ?) WHERE id=?",
                          (facts["demographic"], wid))
            if facts.get("ja_kanji"):
                c.execute("UPDATE work SET native_title=COALESCE(native_title, ?) WHERE id=?",
                          (facts["ja_kanji"], wid))
            # title-page facts: people and lists as JSON, the rest as text
            for field in ("author", "illustrator", "genre", "magazine", "imprint", "ja_kanji", "ja_romaji"):
                val = facts.get(field)
                if val:
                    c.execute("""INSERT OR REPLACE INTO claim
                        (entity,entity_id,field,value,source,source_url,licence,retrieved_at)
                        VALUES('work',?,?,?,'wikipedia',?,?,?)""",
                        (wid, field, json.dumps(val, ensure_ascii=False) if isinstance(val, list) else val,
                         src, LICENCE["wikipedia"], NOW))
            if facts.get("image"):
                # a pointer to Wikipedia's non-free infobox image: restricted, never exported
                c.execute("""INSERT OR REPLACE INTO claim
                    (entity,entity_id,field,value,source,source_url,licence,retrieved_at)
                    VALUES('work',?,'image',?,'wikipedia_image',?,?,?)""",
                    (wid, facts["image"], src, LICENCE["wikipedia_image"], NOW))
            # publisher: the original one onto the JP lines, the English one onto EN lines
            for market, key in (("JP", "publisher"), ("EN", "publisher_en")):
                if facts.get(key):
                    n_pub += c.execute("""UPDATE release_line SET publisher=?
                                          WHERE work_id=? AND market=? AND publisher IS NULL""",
                                       (facts[key], wid, market)).rowcount
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                      ("main:" + wid, json.dumps(facts, ensure_ascii=False)[:500]))
            n_ok += 1
        except Exception as e:
            n_err += 1
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                      ("mainerr:" + wid, ("%s: %s" % (type(e).__name__, e))[:200]))
        if i % 100 == 0:
            db.commit()
            if verbose:
                el = time.time() - t0
                print("  %d/%d ok=%d status=%d publishers=%d err=%d  %.1f/s"
                      % (i, len(works), n_ok, n_status, n_pub, n_err, i / el), flush=True)
    db.commit()
    if verbose:
        print("  main articles: ok=%d with status=%d publisher rows=%d err=%d (%.1f min)"
              % (n_ok, n_status, n_pub, n_err, (time.time() - t0) / 60), flush=True)
    return n_status


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "opentome.db"),
        int(sys.argv[2]) if len(sys.argv) > 2 else None)
