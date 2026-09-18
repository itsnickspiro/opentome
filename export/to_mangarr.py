"""Export OpenTome -> a Mangarr-shaped artifact (drop-in for manga-metadata.sqlite).

Mangarr's `series` table is already one row per RELEASE LINE, which is the same
model OpenTome converged on independently, so the mapping is close to direct:

    opentome.release_line  ->  series
    opentome.volume        ->  volumes
    opentome.work_title    ->  series_alias      (localized-title matching)
    opentome.composition   ->  volumes.composition   (contains='volume' ONLY)

Two constraints come from the C# models, not from SQLite:

  * GcdSeries.GcdSeriesId is `int`  -> series ids must be stable INTEGERS.
    They are hash-derived and recorded in `id_map`, so a rebuild reuses existing
    ids and only new lines get new ones. Sequential numbering was rejected: it
    renumbers everything after an insertion, which the ID contract forbids.

  * GcdVolume.VolumeNumber is `int` -> fractional (7.5) and label volumes
    (SP, Ex3, Side Story) cannot be represented. They are NOT dropped silently:
    they go to `volumes_special`, which today's C# ignores and a later version
    can read, and the count is written to `meta`.

Three lessons from the first export, each measured against the live library
(docs/cleanup-v2.md):

  * `composition` means "the ORIGINAL volumes this volume contains" to the C#
    (an omnibus list). The first export wrote CHAPTER lists into it, which
    flagged every well-documented main line as an omnibus and made Mangarr
    prefer spin-offs over main lines (Attack on Titan -> "Before the Fall").
    Chapters now go to their own column; composition carries volumes only.
  * Work-level titles were attached as aliases to EVERY line of the work, so
    "Attack on Titan" answered 43 series. Only a work's MAIN line per market
    carries the work's titles; sub-lines carry their own name.
  * Year- and month-precision dates were emitted as bare '2019' / '2019-04',
    which .NET parses as 1 January / 1 April. Only day-precision dates are
    emitted as `release_date`; the coarse value and its precision travel in
    separate columns the C# can adopt later.
"""
import hashlib, json, os, re, sqlite3, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tier0"))
sys.path.insert(0, os.path.join(ROOT, "tier2"))
from build_corpus import work_title
from release_lines import GENERIC
from corrections import load_aliases


def _build(name):
    """Default build-output path. Never /tmp: macOS cleaned it and destroyed a
    fully-built catalogue. Outputs belong beside the cache, inside the repo."""
    d = os.path.join(ROOT, "build")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

MARKET_LANG = {"JP": "ja", "EN": "en", "FR": "fr", "DE": "de", "KR": "ko",
               "IT": "it", "ES": "es", "BR": "pt-BR", "CN": "zh", "TW": "zh-TW",
               "HK": "zh-HK"}


def normalize(value):
    """Byte-for-byte mirror of GcdMetadataService.Normalize() in the C#:
    lowercase, collapse every run of non-[a-z0-9] to a single space, trim.

    Mangarr queries `series_alias WHERE alias = @n` with the NORMALIZED form of
    a folder name. Storing only raw titles meant those queries never hit, which
    is why the first export matched fewer series than the hand-curated artifact
    it was meant to replace -- despite holding far more data."""
    out, pending = [], False
    for ch in (value or "").lower():
        if ch.isalnum() and ord(ch) < 128:
            if pending and out:
                out.append(" ")
            pending = False
            out.append(ch)
        else:
            pending = True
    return "".join(out).strip()


def heads(title):
    """Subtitle-stripped forms: 'Frieren: Beyond Journey's End' -> 'Frieren'."""
    t = (title or "").strip()
    out = []
    for sep in (":", " -", " –", ","):
        if sep in t:
            head = t.split(sep)[0].strip()
            if len(head) > 2:
                out.append(head)
    return out


def variants(title, with_heads=True):
    """Alias forms a real folder name might take. Deterministic order: the
    old set-based version flipped case forms between runs with the hash seed
    and produced thousands of spurious artifact diffs."""
    t = (title or "").strip()
    if not t:
        return []
    out = {t, normalize(t)}
    if with_heads:
        for head in heads(t):
            out.add(head)
            out.add(normalize(head))
    return sorted(x for x in out if x)


SCHEMA = """
PRAGMA user_version = 2;
CREATE TABLE IF NOT EXISTS series (
    gcd_series_id INTEGER PRIMARY KEY, name TEXT NOT NULL, year_began INTEGER,
    publisher TEXT, language TEXT, country TEXT,
    is_omnibus INTEGER NOT NULL DEFAULT 0, volume_count INTEGER NOT NULL DEFAULT 0,
    status TEXT, orig_series_id INTEGER,
    anilist_id INTEGER, mangaupdates_id INTEGER, mangadex_id TEXT,
    -- OpenTome additions (ignored by a C# that names its columns explicitly)
    medium TEXT,                 -- manga | light_novel | manhwa | novel | ...
    dated_count INTEGER NOT NULL DEFAULT 0,   -- integer volumes with a day-precision date
    is_main INTEGER NOT NULL DEFAULT 0,       -- the work's main line for this market
    tome_id TEXT,                -- OpenTome release-line id  (public contract)
    tome_work_id TEXT,           -- OpenTome work id
    parent_series_id INTEGER);   -- the series this arc / spin-off line belongs to (collections)
CREATE TABLE IF NOT EXISTS volumes (
    id INTEGER PRIMARY KEY, gcd_series_id INTEGER NOT NULL REFERENCES series(gcd_series_id),
    volume_number INTEGER NOT NULL, title TEXT, release_date TEXT,
    isbn13 TEXT, isbn10 TEXT, page_count INTEGER, composition TEXT,
    release_date_precision TEXT, release_date_raw TEXT, volume_chapters TEXT,
    tome_id TEXT,
    cover_url TEXT, cover_source TEXT,      -- looked up by THIS edition's ISBN; never hosted
    UNIQUE (gcd_series_id, volume_number));
CREATE TABLE IF NOT EXISTS series_alias (
    gcd_series_id INTEGER NOT NULL REFERENCES series(gcd_series_id),
    alias TEXT NOT NULL, UNIQUE (gcd_series_id, alias));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- Not read by the current C#. Preserves volumes whose number is not an int, so
-- nothing is lost silently; a later Mangarr can promote these.
CREATE TABLE IF NOT EXISTS volumes_special (
    gcd_series_id INTEGER NOT NULL, volume_label TEXT NOT NULL, title TEXT,
    release_date TEXT, isbn13 TEXT, page_count INTEGER, composition TEXT,
    UNIQUE (gcd_series_id, volume_label));
-- OpenTome text id <-> Mangarr integer id. Carried across rebuilds so ids never churn.
CREATE TABLE IF NOT EXISTS id_map (
    opentome_id TEXT PRIMARY KEY, int_id INTEGER UNIQUE NOT NULL, kind TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_series_name    ON series (name);
CREATE INDEX IF NOT EXISTS idx_volumes_series ON volumes (gcd_series_id);
CREATE INDEX IF NOT EXISTS idx_alias_alias    ON series_alias (alias);
-- The C# compares COLLATE NOCASE, which a BINARY index cannot serve.
CREATE INDEX IF NOT EXISTS idx_series_name_nc ON series (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_alias_alias_nc ON series_alias (alias COLLATE NOCASE);
"""


def stable_int(text_id, taken):
    """Deterministic positive int32 from an OpenTome id, probing on collision."""
    for salt in range(64):
        h = hashlib.sha256((text_id + ("#%d" % salt if salt else "")).encode()).digest()
        n = int.from_bytes(h[:4], "big") & 0x7FFFFFFF
        if n and n not in taken:
            return n
    raise RuntimeError("could not allocate id for " + text_id)


def _line_raw(lname, wtitle):
    """'Re:Zero (Truth of Zero)' -> 'Truth of Zero'; self-named lines unchanged."""
    if not lname:
        return None
    m = re.match(r"^(.*)\s\((.+)\)$", lname)
    if m and normalize(m.group(1)) == normalize(wtitle):
        return m.group(2).strip()
    return lname


def export(src_path, out_path, carry_ids_from=None):
    src = sqlite3.connect(src_path, timeout=60)
    if os.path.exists(out_path):
        os.remove(out_path)
    out = sqlite3.connect(out_path)
    out.executescript(SCHEMA)

    # reuse existing id assignments if a prior artifact is supplied
    mapping, taken = {}, set()
    if carry_ids_from and os.path.exists(carry_ids_from):
        old = sqlite3.connect(carry_ids_from)
        try:
            for t, i, k in old.execute("SELECT opentome_id,int_id,kind FROM id_map"):
                mapping[t] = i
                taken.add(i)
        except sqlite3.OperationalError:
            pass

    # resolved page counts (BnF / Open Library) -- tier0 never fills volume.page_count
    pages = {}
    for vid, val in src.execute("""SELECT entity_id, value FROM resolution
                                   WHERE entity='volume' AND field='page_count'"""):
        try:
            n = int(float(val))
            if 20 <= n <= 2000:
                pages[vid] = n
        except (TypeError, ValueError):
            pass

    # ISBN-keyed cover URLs (tier1/covers.py). One volume has one ISBN, so at most
    # one claim per source; prefer the source native to the volume's market.
    covers = {}
    for vid, url, source in src.execute("""SELECT entity_id, value, source FROM claim
                                           WHERE entity='volume' AND field='cover_url'"""):
        covers.setdefault(vid, {})[source] = url

    # Work-level facts from the main article (tier0/main_articles.py): status and
    # first year. Mangarr's MapGcdStatus reads completed|ongoing.
    work_facts = {wid: (status, year) for wid, status, year in src.execute(
        "SELECT id, status, year_started FROM work")}

    lines = src.execute("""
        SELECT rl.id, rl.work_id, rl.market, rl.medium, rl.publisher, rl.status, rl.parent_id,
               w.primary_title,
               (SELECT value FROM claim WHERE entity='release_line'
                  AND entity_id=rl.id AND field='line_name') AS line_name
        FROM release_line rl JOIN work w ON w.id=rl.work_id
        ORDER BY rl.id""").fetchall()

    # Every work's normalized title, so a subtitle head that IS another work's
    # title ('Attack on Titan: Before the Fall' -> 'Attack on Titan') is never
    # attached as an alias to the wrong work.
    other_titles = {}
    for wid, t in src.execute("SELECT id, primary_title FROM work"):
        other_titles.setdefault(normalize(t), set()).add(wid)

    # main line per (work, market, medium): the line named after the work, else
    # the biggest. Only main lines carry the work-level aliases.
    by_group = {}
    for rid, wid, market, medium, *_rest, wtitle, lname in lines:
        n = src.execute("SELECT COUNT(*) FROM volume WHERE release_line_id=?", (rid,)).fetchone()[0]
        named = (not lname) or normalize(lname) == normalize(wtitle)
        by_group.setdefault((wid, market, medium), []).append((not named, -n, rid))
    main_of = {k: sorted(v)[0][2] for k, v in by_group.items()}

    # For the status rule: a licensed line's counterpart in the work's original
    # market -- the same-named line there, else that market's main line -- and
    # every line's highest plain-integer volume number. Highest number, not
    # count: an arc line keeps Wikipedia's continuous numbering (34-36) and a
    # table with gaps still reaches its last volume.
    ORIGIN = ("JP", "KR", "CN", "TW")
    markets_of, line_key = {}, {}
    for rid, wid, market, medium, *_rest, wtitle, lname in lines:
        markets_of.setdefault((wid, medium), set()).add(market)
        # exact name, not normalize(): "Kageki Shojo!!" must not resolve to its
        # prequel "Kageki Shojo!", nor "Mechanical Marie" to "Mechanical Marie+"
        line_key[(wid, medium, market, (lname or wtitle).strip().lower())] = rid
    origin_of = {k: next((m for m in ORIGIN if m in ms), None) for k, ms in markets_of.items()}
    int_max = dict(src.execute("""SELECT release_line_id, MAX(CAST(number AS INTEGER)) FROM volume
                                  WHERE number GLOB '[0-9]*' AND number NOT GLOB '*[^0-9]*'
                                  GROUP BY 1"""))

    def origin_reach(wid, medium, market, lname, wtitle):
        """Highest volume number of this line's original-market counterpart."""
        om = origin_of.get((wid, medium))
        if om is None or om == market:
            return None
        rid = (line_key.get((wid, medium, om, (lname or wtitle).strip().lower()))
               or main_of.get((wid, om, medium)))
        return int_max.get(rid, 0) if rid else None

    n_series = n_vol = n_special = n_alias = n_omni = 0
    # ids first, so a child line can point at its parent whichever comes first
    for rid, *_ in lines:
        if rid not in mapping:
            mapping[rid] = stable_int(rid, taken)
            taken.add(mapping[rid])
    # the line NAMED after the work per (work, market, medium): what an arc or
    # spin-off line belongs to when the splitter did not record a parent
    named_line = {}
    for rid, wid, market, medium, *_rest, wtitle, lname in lines:
        if (not lname) or normalize(lname) == normalize(wtitle):
            named_line.setdefault((wid, market, medium), rid)

    for rid, wid, market, medium, publisher, status, parent_rl, wtitle, lname in lines:
        sid = mapping[rid]
        is_main = 1 if main_of.get((wid, market, medium)) == rid else 0
        is_named = (not lname) or normalize(lname) == normalize(wtitle)
        parent_sid = mapping.get(parent_rl or (None if is_named else named_line.get((wid, market, medium))))
        if parent_sid == sid:
            parent_sid = None

        vols = src.execute("""SELECT id, number, title, release_date, release_date_precision,
                                     isbn13, isbn10, format
                              FROM volume WHERE release_line_id=? ORDER BY rowid""", (rid,)).fetchall()
        comp_vol, comp_ch = {}, {}
        for vid, contains, ref_list in src.execute(
                """SELECT volume_id, contains, ref_list FROM composition c
                   WHERE c.volume_id IN (SELECT id FROM volume WHERE release_line_id=?)""", (rid,)):
            (comp_vol if contains == "volume" else comp_ch)[vid] = ref_list
        ints_written, dated, years, is_omni = set(), 0, [], 0
        for vid, num, title, rdate, prec, i13, i10, fmt in vols:
            c = comp_vol.get(vid)
            cv = covers.get(vid) or {}
            cover_src = ("correction" if "correction" in cv else          # a picked cover wins
                         "openbd" if market == "JP" and "openbd" in cv else
                         next(iter(cv), None))
            cover_url = cv.get(cover_src) if cover_src else None
            day = rdate if (rdate and prec == "day" and len(rdate) == 10) else None
            if rdate:
                years.append(int(rdate[:4]))
            try:
                iv = int(num)
                if iv < 0:
                    raise ValueError
            except (TypeError, ValueError):
                out.execute("""INSERT OR IGNORE INTO volumes_special
                    (gcd_series_id,volume_label,title,release_date,isbn13,page_count,composition)
                    VALUES(?,?,?,?,?,?,?)""", (sid, str(num), title, rdate, i13, pages.get(vid), c))
                n_special += 1
                continue
            cur = out.execute("""INSERT OR IGNORE INTO volumes
                (gcd_series_id,volume_number,title,release_date,isbn13,isbn10,page_count,
                 composition,release_date_precision,release_date_raw,volume_chapters,tome_id,
                 cover_url,cover_source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, iv, None, day, i13, i10, pages.get(vid), c, prec, rdate,
                 comp_ch.get(vid), vid, cover_url, cover_src))
            if cur.rowcount:
                ints_written.add(iv)
                n_vol += 1
                if day:
                    dated += 1
                # omnibus iff a READABLE volume genuinely contains >1 original
                # volume; a range-labelled special ('17-18') does not count
                if c and len(json.loads(c)) > 1:
                    is_omni = 1
        n_omni += is_omni
        # A straight-translation line carries no composition in the published file
        # (the pipeline writes `contains` only for omnibus lines; a corrected line
        # writes `[N]` for every volume so the cross-market mapping can see it).
        # Nulling it here keeps `is_omnibus=0 ⇒ composition IS NULL` true for every
        # line -- the artifact contract's rule -- and touches no existing line.
        if not is_omni:
            out.execute("UPDATE volumes SET composition=NULL WHERE gcd_series_id=? AND composition IS NOT NULL", (sid,))

        # volume_count = rows a consumer can actually read. Counting every row
        # (specials, duplicates) made Mangarr create Books with nothing behind them.
        w_status, w_year = work_facts.get(wid, (None, None))
        # Mangarr reads completed|ongoing (MapGcdStatus). The work's status is the
        # original run's; a licensed line is only "completed" once it has reached
        # the original's last volume -- Gintama's English edition stopped at 23 of
        # 77 and is not finished, it is stalled. Reach counts contained originals
        # too, so a 3-in-1 line that covers everything is complete at a third of
        # the count.
        # Only a line named after the work carries the work's status: an arc or
        # spin-off line ("Re:Zero (A Day in the Capital)", finished 2015) would
        # otherwise inherit the ongoing light novel's, and Mangarr's own fallback
        # (AniList knows the arc) is better than a confident wrong value.
        st = status or (w_status if is_named else None)
        oc = origin_reach(wid, medium, market, lname, wtitle)
        if st == "ended" and oc:
            reach = set(ints_written)
            for cj in comp_vol.values():
                reach.update(n for n in json.loads(cj) if isinstance(n, int))
            if (max(reach) if reach else 0) < oc:
                st = "ongoing"
        mangarr_status = {"ended": "completed", "ongoing": "ongoing"}.get(st or "", None)
        out.execute("""INSERT OR REPLACE INTO series
            (gcd_series_id,name,year_began,publisher,language,is_omnibus,volume_count,status,
             medium,dated_count,is_main,tome_id,tome_work_id,parent_series_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, lname or wtitle, min(years) if years else w_year, publisher,
             MARKET_LANG.get(market, market.lower()), is_omni, len(ints_written), mangarr_status,
             medium, dated, is_main, rid, wid, parent_sid))
        out.execute("INSERT OR REPLACE INTO id_map VALUES(?,?, 'release_line')", (rid, sid))
        n_series += 1

        # ---- aliases -------------------------------------------------------
        # Main line: the work's titles in every language (raw article names AND
        # the cleaned work title -- raw names like "Liste des chapitres de X"
        # match no folder, cleaned ones do), plus subtitle-stripped heads unless
        # the head is some OTHER work's title.
        # Sub-line: only its own name and its bare sub-title when that is
        # specific enough (3+ tokens, not a generic heading) -- so a folder named
        # after the arc ("A Day in the Capital") can still resolve.
        # EVERY line carries its own name, and a sub-line also carries its bare
        # arc title ("Re:Zero (Truth of Zero)" -> "Truth of Zero"). The main
        # line ADDITIONALLY carries the work's titles in every language, plus
        # the official-English and redirect titles main_titles.py collected.
        #
        # These used to be either/or, so whichever arc happened to be the
        # market's main line lost its arc alias and became unreachable by the
        # only name a folder ever uses -- Re:Zero's "The Sanctuary and the Witch
        # of Greed" resolved to nothing at all.
        cands = [(lname or wtitle, False)]
        raw = _line_raw(lname, wtitle)
        if raw and raw != lname and len(normalize(raw).split()) >= 3 and not GENERIC.match(raw):
            cands.append((raw, False))
        if is_main:
            for (alias,) in src.execute("SELECT title FROM work_title WHERE work_id=?", (wid,)):
                if alias:
                    cands.append((alias, True))
                    cands.append((work_title(alias), True))
            cands.append((wtitle, True))

        def owned_by_another_work(text):
            """True when this string is some OTHER work's own title -- the
            guard that keeps 'Attack on Titan' off 'Attack on Titan: Before the
            Fall', and now also keeps a shared redirect off the wrong work."""
            return bool(other_titles.get(normalize(text), set()) - {wid})

        seen = set()
        for alias, work_level in cands:
            # A work-level alias is only as trustworthy as its uniqueness; a
            # line's own name is always kept, since that IS what it is called.
            if work_level and owned_by_another_work(alias):
                continue
            for a in variants(alias, with_heads=False):
                if a.lower() not in seen:
                    seen.add(a.lower())
                    out.execute("INSERT OR IGNORE INTO series_alias VALUES(?,?)", (sid, a))
                    n_alias += 1
            if not work_level:
                continue
            for head in heads(alias):
                if owned_by_another_work(head):
                    continue          # 'Attack on Titan' belongs to another work
                for a in (head, normalize(head)):
                    if a and a.lower() not in seen:
                        seen.add(a.lower())
                        out.execute("INSERT OR IGNORE INTO series_alias VALUES(?,?)", (sid, a))
                        n_alias += 1

    # Hand-checked alias corrections (corrections/aliases.json). Applied last so
    # a correction always reaches the artifact, and asserted by test_artifact.py
    # so one that stops landing fails the build instead of vanishing quietly.
    n_corr = 0
    for line_id, alias in load_aliases():
        sid = mapping.get(line_id)
        if sid is None or not out.execute(
                "SELECT 1 FROM series WHERE gcd_series_id=?", (sid,)).fetchone():
            raise SystemExit(
                "corrections/aliases.json: release line %s is not in this catalogue --\n"
                "fix or remove the entry (see corrections/README.md)" % line_id)
        for a in variants(alias, with_heads=False):
            n_corr += out.execute(
                "INSERT OR IGNORE INTO series_alias VALUES(?,?)", (sid, a)).rowcount

    src_counts = dict(src.execute("SELECT source, COUNT(*) FROM claim GROUP BY source"))
    for k, v in [
        ("schema_version", "2"),
        ("generator", "opentome"),
        ("generated_at", NOW),
        ("source", "OpenTome — reconciled from Wikipedia, openBD, Open Library, BnF"),
        # BnF's Etalab licence and openBD's terms both REQUIRE retained attribution.
        # Names only the sources the pipeline actually reads (DNB is not wired in yet);
        # LICENSE-DATA.md carries this string byte-for-byte -- change both together.
        ("attribution", "Bibliographic data: Bibliotheque nationale de France (Licence Ouverte/Open Licence); "
                        "openBD; Open Library / Internet Archive; "
                        "Wikipedia contributors (facts only). Cover art is not included."),
        ("licence", "Free/non-commercial use. openBD and Open Library terms are non-commercial; "
                    "see docs/legal-position.md before any paid use."),
        ("gcd_dump", "opentome-" + NOW[:10]),
        ("volumes_special_count", str(n_special)),
        ("omnibus_lines", str(n_omni)),
        ("correction_aliases", str(len(load_aliases()))),
        # Publishing is gated on this being exactly "opentome": merge_aliases.py
        # overwrites it with the name of any artifact it merged aliases from, so
        # a build that is not clean-room fails the check rather than passing it
        # by omission. Fail-closed, not fail-open.
        ("alias_provenance", "opentome"),
        ("claim_sources", json.dumps(src_counts)),
        ("composition_semantics", "volumes.composition = original-market volume numbers this "
                                  "volume contains (omnibus). Chapters are in volume_chapters."),
        ("release_date_semantics", "release_date is day-precision only; coarser values are in "
                                   "release_date_raw with release_date_precision."),
    ]:
        out.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
    out.commit()
    return dict(series=n_series, volumes=n_vol, specials=n_special, aliases=n_alias,
                omnibus_lines=n_omni)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else _build("opentome.db")
    out = sys.argv[2] if len(sys.argv) > 2 else _build("manga-metadata.sqlite")
    carry = sys.argv[3] if len(sys.argv) > 3 else None
    r = export(src, out, carry)
    print("exported -> %s" % out)
    for k, v in r.items():
        print("   %-14s %s" % (k, format(v, ",")))
    print("   size           %.0f MB" % (os.path.getsize(out) / 1e6))
