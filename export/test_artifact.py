"""Contract tests for the exported Mangarr artifact.

Run automatically by tier0/rebuild_all.sh. Each rule encodes something the C#
consumer assumes and the first export violated silently while every unit test
on both sides stayed green (docs/cleanup-v2.md):

  * composition = ORIGINAL volume numbers this volume contains; a series is
    omnibus iff some volume contains >1 of them
  * volume_count = rows a consumer can read (Mangarr creates Books 1..N)
  * release_date is day precision or NULL (never '2019' -> 1 January)
  * no wiki markup in names, aliases or publishers
  * no English/French series with zero data
"""
import json, os, re, sqlite3, sys

FAILS = []


def rule(label, n, detail=""):
    ok = n == 0
    print(("  ok   " if ok else "  FAIL ") + f"{label}: {n:,}" + (f"  {detail}" if not ok else ""))
    if not ok:
        FAILS.append(label)


def run(path):
    db = sqlite3.connect(path)
    g = lambda q: db.execute(q).fetchone()[0]

    rule("meta.gcd_dump missing or not opentome-YYYY-MM-DD",
         0 if re.fullmatch(r"opentome-\d{4}-\d{2}-\d{2}",
                           g("SELECT COALESCE((SELECT value FROM meta WHERE key='gcd_dump'),'')")) else 1)

    # composition semantics
    rule("is_omnibus=0 series with a composition",
         g("""SELECT COUNT(DISTINCT s.gcd_series_id) FROM series s JOIN volumes v USING(gcd_series_id)
              WHERE s.is_omnibus=0 AND v.composition IS NOT NULL"""))
    rule("is_omnibus=1 series with no multi-entry composition",
         g("""SELECT COUNT(*) FROM series s WHERE s.is_omnibus=1 AND NOT EXISTS
              (SELECT 1 FROM volumes v WHERE v.gcd_series_id=s.gcd_series_id
               AND v.composition IS NOT NULL AND LENGTH(v.composition)>3)"""))
    bad_comp = 0
    for (c,) in db.execute("SELECT composition FROM volumes WHERE composition IS NOT NULL"):
        try:
            lst = __import__("json").loads(c)
            if not (isinstance(lst, list) and all(isinstance(x, int) for x in lst) and 1 <= len(lst) <= 12):
                bad_comp += 1
        except Exception:
            bad_comp += 1
    rule("composition not a short list of ints", bad_comp)
    rule("chapter lists leaked into composition (len > 12)",
         g("""SELECT COUNT(*) FROM volumes WHERE composition IS NOT NULL
              AND LENGTH(composition) - LENGTH(REPLACE(composition, ',', '')) >= 12"""))

    # counts
    rule("series.volume_count != rows in volumes",
         g("""SELECT COUNT(*) FROM series s WHERE s.volume_count <>
              (SELECT COUNT(*) FROM volumes v WHERE v.gcd_series_id=s.gcd_series_id)"""))
    rule("volumes with volume_number < 0", g("SELECT COUNT(*) FROM volumes WHERE volume_number < 0"))

    # dates
    rule("release_date not day precision (must be NULL or YYYY-MM-DD)",
         g("""SELECT COUNT(*) FROM volumes WHERE release_date IS NOT NULL
              AND (LENGTH(release_date)<>10 OR release_date_precision<>'day')"""))
    rule("Jan-1 release dates (year-only tell)",
         g("SELECT COUNT(*) FROM volumes WHERE release_date LIKE '%-01-01'"))

    # names / aliases
    rule("series names with wiki markup",
         g("""SELECT COUNT(*) FROM series WHERE name LIKE '%{{%' OR name LIKE '%[[%'
              OR name LIKE '%<%' OR name LIKE '%}}%'"""))
    rule("aliases with wiki markup",
         g("""SELECT COUNT(*) FROM series_alias WHERE alias LIKE '%{{%' OR alias LIKE '%[[%'
              OR alias LIKE '%<%'"""))
    rule("empty series names", g("SELECT COUNT(*) FROM series WHERE TRIM(name)=''"))
    rule("publishers with markup",
         g("""SELECT COUNT(*) FROM series WHERE publisher LIKE '%<%' OR publisher LIKE '%{{%'
              OR publisher LIKE '%}}%' OR publisher LIKE '%[[%'"""))

    # phantom lines: no date of ANY precision and no ISBN (release_date is
    # day-only; a month/year value lives in release_date_raw and is data)
    rule("en/fr/de series with no date (any precision) and no ISBN",
         g("""SELECT COUNT(*) FROM series s WHERE s.language IN ('en','fr','de')
              AND s.volume_count>0 AND NOT EXISTS (SELECT 1 FROM volumes v
              WHERE v.gcd_series_id=s.gcd_series_id
              AND (v.release_date_raw IS NOT NULL OR v.isbn13 IS NOT NULL))"""))
    rule("un-collapsed omnibus rows (consecutive, same ISBN, same date) in a non-ja series",
         g("""SELECT COUNT(*) FROM volumes a JOIN volumes b ON b.gcd_series_id=a.gcd_series_id
              AND b.isbn13=a.isbn13
              AND COALESCE(b.release_date_raw,'')=COALESCE(a.release_date_raw,'')
              AND b.volume_number=a.volume_number+1
              JOIN series s ON s.gcd_series_id=a.gcd_series_id
              WHERE s.language<>'ja' AND a.isbn13 IS NOT NULL"""))
    print("  info  same ISBN on rows with different dates / gaps (upstream, not merged): %s" % format(
        g("""SELECT COUNT(*) FROM (SELECT v.gcd_series_id, v.isbn13 FROM volumes v
             JOIN series s USING(gcd_series_id) WHERE s.language<>'ja' AND v.isbn13 IS NOT NULL
             GROUP BY 1,2 HAVING COUNT(*)>1)"""), ","))

    # corrections must actually land -- the whole point of keeping them as data
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "corrections", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tier2", "corrections.py"))
    corr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corr)

    missing_alias = 0
    for line_id, alias in corr.load_aliases():
        hit = db.execute("""SELECT 1 FROM series_alias a JOIN series s USING(gcd_series_id)
                            WHERE s.tome_id=? AND a.alias=? COLLATE NOCASE""",
                         (line_id, alias)).fetchone()
        missing_alias += not hit
    rule("alias corrections missing from the artifact", missing_alias)

    missing_value, checked_value = 0, 0
    for e in corr._read("volumes.json"):
        key, field, want = str(e["volume"]).strip(), e["field"], str(e["value"])
        if field not in ("release_date", "isbn13", "page_count", "title", "cover_url"):
            continue
        col = "release_date" if field == "release_date" else field
        row = db.execute(
            "SELECT %s FROM volumes WHERE tome_id=? OR isbn13=?" % col,
            (key, re.sub(r"[^0-9Xx]", "", key))).fetchone()
        if row is None:
            continue        # non-integer volume label -> volumes_special, not read here
        checked_value += 1
        missing_value += str(row[0]) != want
    rule("volume corrections not present in the artifact", missing_value,
         f"({checked_value} checked)")

    missing_line = 0
    for e in corr._read("lines.json"):
        rid = corr._id("rl_", e["work"], e["medium"], e["market"].upper(), e["name"].strip())
        want = sum(1 for v in e["volumes"] if str(v.get("number", "")).strip().isdigit())
        row = db.execute("SELECT volume_count FROM series WHERE tome_id=?", (rid,)).fetchone()
        missing_line += (row is None or row[0] < want)
    rule("line corrections missing from the artifact", missing_line)

    # status / covers (schema_version 2 additions)
    rule("licensed line 'completed' while behind its same-named original-market line",
         g("""SELECT COUNT(*) FROM series l JOIN series o
              ON o.tome_work_id=l.tome_work_id AND o.medium=l.medium AND o.name=l.name
              AND o.language IN ('ja','ko','zh') AND l.language NOT IN ('ja','ko','zh')
              WHERE l.status='completed' AND l.is_omnibus=0
              AND (SELECT MAX(volume_number) FROM volumes WHERE gcd_series_id=l.gcd_series_id)
                < (SELECT MAX(volume_number) FROM volumes WHERE gcd_series_id=o.gcd_series_id)"""))
    rule("parent_series_id pointing at a missing series",
         g("SELECT COUNT(*) FROM series c WHERE parent_series_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM series p WHERE p.gcd_series_id=c.parent_series_id)"))
    rule("parent_series_id pointing at a series of another work or market",
         g("""SELECT COUNT(*) FROM series c JOIN series p ON p.gcd_series_id=c.parent_series_id
              WHERE p.tome_work_id<>c.tome_work_id OR p.language<>c.language"""))
    print("  info  series with a parent (collection members): %s" % format(g("SELECT COUNT(*) FROM series WHERE parent_series_id IS NOT NULL"), ","))
    rule("series.status outside {completed, ongoing, NULL}",
         g("SELECT COUNT(*) FROM series WHERE status IS NOT NULL AND status NOT IN ('completed','ongoing')"))
    rule("cover_url without cover_source (or vice versa)",
         g("""SELECT COUNT(*) FROM volumes WHERE (cover_url IS NULL) <> (cover_source IS NULL)"""))
    rule("cover_url that is not http(s)",
         g("SELECT COUNT(*) FROM volumes WHERE cover_url IS NOT NULL AND cover_url NOT LIKE 'http%'"))
    # AniList ids (export/resolve_anilist.py, 2026-09-15): Mangarr binds by this id BEFORE any
    # title search, so the lines people actually add must carry one, and one AniList entry
    # can only be one work -- an id on two works is a wrong bind on at least one of them.
    # The allowance is 15 % (measured 13.1 % on opentome-2026-09-04; the residue is catalogue
    # naming -- Wikipedia list-article names, franchises AniList lists per part -- that no
    # exact-equality rule can reach, and a guess would be pinned by every future add).
    en3 = g("SELECT COUNT(*) FROM series WHERE language='en' AND volume_count>=3")
    en3_missing = g("SELECT COUNT(*) FROM series WHERE language='en' AND volume_count>=3 AND anilist_id IS NULL")
    rule("EN lines (volume_count >= 3) without anilist_id beyond the 15 % allowance",
         max(0, en3_missing - en3 * 15 // 100), f"({en3_missing:,} of {en3:,} unresolved)")
    # Report-only (ruled 2026-09-15): on opentome-2026-09-04 every shared id but one is the same
    # spin-off modelled twice in the catalogue (a parent-nested "(Before the Fall)" line AND the
    # article's own work) and the other is Dragon Ball / Dragon Ball Z -- two works, one AniList
    # entry. Both are catalogue shape, not a resolver fault; a genuinely wrong bind shows up here
    # too, so the pairs are printed for reading, never gated.
    shared = db.execute("""SELECT s.anilist_id, group_concat(s.name, ' | ') FROM series s
              WHERE s.language='en' AND s.anilist_id IN (SELECT anilist_id FROM series WHERE language='en'
              AND anilist_id IS NOT NULL GROUP BY anilist_id HAVING COUNT(DISTINCT tome_work_id)>1)
              GROUP BY 1 ORDER BY 1""").fetchall()
    print("  info  anilist_id shared by EN lines of different works: %d" % len(shared))
    for aid, names in shared:
        print("        %s: %s" % (aid, names))
    print("  info  EN lines with anilist_id: %s / %s (volume_count >= 3: %s / %s)" % (
        format(g("SELECT COUNT(*) FROM series WHERE language='en' AND anilist_id IS NOT NULL"), ","),
        format(g("SELECT COUNT(*) FROM series WHERE language='en'"), ","),
        format(en3 - en3_missing, ","), format(en3, ",")))
    print("  info  series with status: %s | volumes with an ISBN-keyed cover: %s | publishers set: %s" % (
        format(g("SELECT COUNT(*) FROM series WHERE status IS NOT NULL"), ","),
        format(g("SELECT COUNT(*) FROM volumes WHERE cover_url IS NOT NULL"), ","),
        format(g("SELECT COUNT(*) FROM series WHERE publisher IS NOT NULL"), ",")))

    # ids
    rule("series without an id_map row",
         g("""SELECT COUNT(*) FROM series s WHERE NOT EXISTS
              (SELECT 1 FROM id_map m WHERE m.int_id=s.gcd_series_id)"""))
    rule("more than one main line per (work, language, medium)",
         g("""SELECT COUNT(*) FROM (SELECT tome_work_id, language, medium FROM series
              WHERE is_main=1 GROUP BY 1,2,3 HAVING COUNT(*)>1)"""))

    # informational
    print("  info  series %s / volumes %s / aliases %s / omnibus lines %s / specials %s" % (
        format(g("SELECT COUNT(*) FROM series"), ","),
        format(g("SELECT COUNT(*) FROM volumes"), ","),
        format(g("SELECT COUNT(*) FROM series_alias"), ","),
        format(g("SELECT COUNT(*) FROM series WHERE is_omnibus=1"), ","),
        format(g("SELECT COUNT(*) FROM volumes_special"), ",")))
    amb = g("""SELECT COUNT(*) FROM (SELECT LOWER(alias) a FROM series_alias sa
               JOIN series s USING(gcd_series_id) WHERE s.language='en'
               GROUP BY a HAVING COUNT(DISTINCT gcd_series_id)>1)""")
    print(f"  info  en aliases shared by >1 en series: {amb:,}")
    return FAILS


if __name__ == "__main__":
    fails = run(sys.argv[1])
    print()
    if fails:
        print(f"{len(fails)} contract rule(s) FAILED: {fails}")
        sys.exit(1)
    print("artifact contract ok")
