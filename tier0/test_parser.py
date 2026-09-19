"""Unit tests for the tier-0 parser. Run: python3 tier0/test_parser.py

Every case here is a shape that was measured in the corpus and mis-handled
before (docs/cleanup-v2.md). No network: everything is synthetic wikitext.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wikipedia_volumes import parse_date, canon_number, parse_volumes, _is_blank, FR_MONTHS
from isbn import isbn_market, normalise_isbn
from collapse import collapse_licensed
import release_lines as RL
import main_articles as MA

FAILS = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"  got={got!r} want={want!r}"))
    if not ok:
        FAILS.append(label)


# ---- dates -------------------------------------------------------------
eq("plain mdy", parse_date("October 13, 2013<ref>x</ref>"), ("2013-10-13", "day"))
eq("mdy no comma", parse_date("May 4 2020"), ("2020-05-04", "day"))
eq("3-letter month", parse_date("Nov 3, 2021"), ("2021-11-03", "day"))
eq("{{start date}}", parse_date("{{start date|2023|2|9}}"), ("2023-02-09", "day"))
eq("{{Start date|df}}", parse_date("{{Start date|2023|2|9|df=y}}"), ("2023-02-09", "day"))
eq("{{dts}} numeric", parse_date("{{dts|2020|4|21}}"), ("2020-04-21", "day"))
eq("{{dts}} text", parse_date("{{dts|April 21, 2020}}"), ("2020-04-21", "day"))
eq("fr {{date|d|mois|y}}", parse_date("{{date|16|juillet|2010}}", FR_MONTHS), ("2010-07-16", "day"))
eq("fr {{Date|d mois y}}", parse_date("{{Date|16 septembre 2010}}", FR_MONTHS), ("2010-09-16", "day"))
eq("fr {{Date|dd|MM|yyyy}}", parse_date("{{Date|10|03|1998}}", FR_MONTHS), ("1998-03-10", "day"))
eq("fr 1er", parse_date("{{date|1er|janvier|2010}}", FR_MONTHS), ("2010-01-01", "day"))
eq("fr {{date rapide}}", parse_date("{{date rapide|2010|3|17}}", FR_MONTHS), ("2010-03-17", "day"))
eq("fr {{date-}}", parse_date("{{date-|2 octobre 1975}}", FR_MONTHS), ("1975-10-02", "day"))
eq("month only", parse_date("September 2003"), ("2003-09", "month"))
eq("year only", parse_date("2019"), ("2019", "year"))
eq("invalid day degrades", parse_date("February 30, 2020"), ("2020-02", "month"))
eq("dash is none", parse_date("—"), (None, "none"))
eq("TBA is none", parse_date("TBA"), (None, "none"))

# ---- placeholders ------------------------------------------------------
for v in ("", " ", "—", "-", "&mdash;", "N/A", "N/A (Part of omnibus volume 3)", "TBA", "{{nc}}", "?"):
    eq(f"blank {v!r}", _is_blank(v), True)
eq("not blank", _is_blank("978-1-61262-420-4"), False)

# ---- volume labels -----------------------------------------------------
eq("01 -> 1", canon_number("01")[:2], ("1", "int"))
eq("1.0 -> 1", canon_number("1.0")[:2], ("1", "int"))
eq("1 (18) dual", canon_number("1 (18)")[:2], ("1", "int"))
eq("Volume 3", canon_number("Volume 3")[:2], ("3", "int"))
eq("9 RE:", canon_number("9 RE:")[:2], ("9", "int"))
eq("7.5 decimal", canon_number("7.5")[:2], ("7.5", "decimal"))
eq("17-18 range", canon_number("17-18")[:2], ("17-18", "range"))
eq("SP special", canon_number("SP")[:2], ("SP", "special"))
eq("Ex3 special", canon_number("Ex3")[:2], ("Ex3", "special"))
eq("dash blank", canon_number("—")[1], "blank")
eq("katakana dash blank", canon_number("ー")[1], "blank")

# ---- isbn --------------------------------------------------------------
eq("978-4 JP", isbn_market("9784063842760"), "JP")
eq("978-1 EN", isbn_market("9781612624204"), "EN")
eq("979-8 EN", isbn_market("9798888779347"), "EN")
eq("979-11 KR", isbn_market("9791170336662"), "KR")
eq("979-10 FR", isbn_market("9791030000000"), "FR")
eq("978-89 KR", isbn_market("9788912345678"), "KR")
eq("978-2 FR", isbn_market("9782723456789"), "FR")
eq("JAN rejected", normalise_isbn("4573000000000"), (None, None))
eq("isbn10 -> 13", normalise_isbn("4088735048")[0], "9784088735047")

# ---- emission rule -----------------------------------------------------
W = """
{{Graphic novel list/header |Language = Japanese |SecondLanguage = English}}
{{Graphic novel list
 | VolumeNumber    = 1
 | OriginalRelDate = March 17, 2010
 | OriginalISBN    = 978-4-06-384276-0
 | LicensedRelDate = June 19, 2012
 | LicensedISBN    = 978-1-61262-024-4
}}
{{Graphic novel list
 | VolumeNumber    = 02
 | OriginalRelDate = {{start date|2010|7|16}}
 | OriginalISBN    = 978-4-06-384299-9
 | LicensedRelDate = —
 | LicensedISBN    =
}}
{{Graphic novel list
 | VolumeNumber    = 3
 | OriginalRelDate =
 | OriginalISBN    =
 | LicensedRelDate = N/A (Part of omnibus volume 1)
 | LicensedISBN    =
 | Title           = Announced Title
}}
{{Graphic novel list
 | VolumeNumber    = 4
 | RelDate         = January 5, 2020
 | ISBN            = 979-11-7033-666-2
 | LicensedRelDate = March 2, 2021
 | LicensedISBN    = 978-1-9753-1943-4
}}
{{Graphic novel list/footer}}
"""
recs = parse_volumes(W, "List of Test chapters", "en")
eq("4 rows parsed", len(recs), 4)
r1, r2, r3, r4 = recs
eq("row1 both markets", sorted(r1["markets"]), ["licensed", "original"])
eq("row1 JP market", r1["markets"]["original"]["market"], "JP")
eq("row2 number canon", r2["volume"], "2")
eq("row2 start-date parsed", r2["markets"]["original"].get("date"), "2010-07-16")
eq("row2 NO phantom EN entry", "licensed" in r2["markets"], False)
eq("row3 announced JP only", (r3.get("announced"), list(r3["markets"])), (True, ["original"]))
eq("row3 title kept", r3.get("title"), "Announced Title")
eq("row4 Korean original -> KR", r4["markets"]["original"]["market"], "KR")
eq("row4 EN licensed", r4["markets"]["licensed"]["market"], "EN")

FRW = """
{{TomeBD|volume=1|langage_unique=oui|sortie_1={{date|16|juillet|2010}}|isbn_1=978-4-06-384276-0|sortie_2=|isbn_2=}}
{{TomeBD|volume=2|langage_unique=non|sortie_1=2010|isbn_1=978-4-06-384299-9|sortie_2={{Date|10|03|1998}}|isbn_2=978-2-7234-5678-9}}
"""
fr = parse_volumes(FRW, "Liste des chapitres de Test", "fr")
eq("fr single-language skips licensed", list(fr[0]["markets"]), ["original"])
eq("fr licensed FR numeric date", fr[1]["markets"]["licensed"].get("date"), "1998-03-10")

# ---- omnibus collapse --------------------------------------------------
def rec(n, isbn, date, pos):
    return {"volume": str(n), "_offset": pos, "medium": "manga", "line": "X",
            "markets": {"original": {"market": "JP", "isbn13": f"978400000000{n}"},
                        "licensed": {"market": "EN", "isbn13": isbn, "date": date}}}
vin = [rec(1, "A", "2013-10-13", 10), rec(2, "A", "2013-10-13", 20),
       rec(3, "B", "2014-01-21", 30), rec(4, "B", "2014-01-21", 40),
       rec(5, "C", "2026-10-06", 50)]
out = collapse_licensed(vin)
lic = [(r["volume"], r["markets"].get("licensed")) for r in out]
eq("collapse keeps JP rows", len(out), 5)
eq("collapse: 3 EN volumes", sum(1 for _, m in lic if m), 3)
eq("collapse: EN renumbered", [m["number"] for _, m in lic if m], ["1", "2", "3"])
eq("collapse: composition (single row maps too)", [m.get("contains") for _, m in lic if m], [[1, 2], [3, 4], [5]])
eq("collapse: first-of-run keeps date", lic[0][1]["date"], "2013-10-13")
# a line with no runs is untouched
plain = [rec(1, "A", "2020", 1), rec(2, "B", "2021", 2)]
eq("no-run line untouched", [(r["markets"]["licensed"].get("number"), r["markets"]["licensed"].get("contains")) for r in collapse_licensed(plain)], [(None, None), (None, None)])
# JP-only duplicate ISBNs never collapse (different dates => attribution error, not omnibus)
jp = [rec(1, "A", "2020-01-01", 1), rec(2, "A", "2021-01-01", 2)]
eq("different dates: no collapse", [r["markets"]["licensed"].get("contains") for r in collapse_licensed(jp)], [None, None])

# ---- arc split ------------------------------------------------------
def arc(n, title, pos):
    return {"volume": str(n), "_offset": pos, "medium": "manga", "line": "SAO: Progressive",
            "title": title, "markets": {"original": {"market": "JP", "isbn13": f"978400000{n:04d}"}}}
rows = [arc(i, f"SAO: Progressive {i}", i * 10) for i in range(1, 4)]
rows += [arc(4, "SAO: Progressive: Barcarolle of Froth 1", 40), arc(5, "SAO: Progressive: Barcarolle of Froth 2", 50)]
rows += [arc(6, "SAO: Progressive: Oddity 1", 60)]              # single row: NOT a run
rows += [arc(7, "SAO: Progressive 7", 70)]
sp = RL.split_arcs(rows, "SAO")
eq("arc: base keeps its rows", [r["volume"] for r in sp if r["line"] == "SAO: Progressive"], ["1", "2", "3", "6", "7"])
eq("arc: run becomes its own line", [r["volume"] for r in sp if "Barcarolle" in r["line"]], ["1", "2"])
eq("arc: named after the work", sp[3]["line"], "SAO: Progressive: Barcarolle of Froth")
eq("arc: raw name is the arc alone", sp[3]["line_raw"], "Barcarolle of Froth")
eq("arc: single odd row left alone", sp[5]["line"], "SAO: Progressive")
eq("arc: parent recorded", sp[3].get("arc_of"), "SAO: Progressive")
eq("arc: 'Livre'/'Book' is a number word", RL._stem("Nisemonogatari - Légendes Illusoires : Livre 2"), ("Nisemonogatari - Légendes Illusoires", 2))
eq("arc: 'Book' is a number word", RL._stem("Ascendance of a Bookworm Part 2 Book 3")[1], 3)
w = [arc(i, f"W.I.T.C.H. {i}", i * 10) for i in range(1, 3)]
w += [arc(3, "Part IX. 100% W.I.T.C.H. 1", 30), arc(4, "Part IX. 100% W.I.T.C.H. 2", 40)]
for r in w:
    r["line"] = "W.I.T.C.H."
eq("arc: stem containing (not starting with) the work is qualified",
   RL.split_arcs(w, "W.I.T.C.H.")[2]["line"], "W.I.T.C.H. (Part IX. 100% W.I.T.C.H.)")

# ---- main-article detection -------------------------------------------
import main_titles as MT
eq("lead: first italic link when it names the work",
   MT.lead_link("''[[Attack on Titan]]'' is a manga", "Attack on Titan"), ("Attack on Titan", "Attack on Titan"))
eq("lead: magazine first, work later -> the work",
   MT.lead_link("''[[Weekly Shōnen Jump]]'' ran ''[[One Piece]]''", "One Piece"), ("One Piece", "One Piece"))
eq("lead: magazine only -> no main article",
   MT.lead_link("''[[Weekly Shōnen Jump]]'' only", "One Piece"), (None, None))
eq("lead: generic 'tankōbon' skipped",
   MT.lead_link("''[[tankōbon]]'' cover. ''[[Dragon Ball (manga)|Dragon Ball]]'' is", "Dragon Ball"),
   ("Dragon Ball (manga)", "Dragon Ball"))
eq("lead: italics inside the link display",
   MT.lead_link("[[Aria (manga)|''Aqua'' and ''Aria'']] is a manga", "Aria"), ("Aria (manga)", "Aqua and Aria"))
eq("lead: spin-off never takes its parent",
   MT.lead_link("''[[Dragon Ball (manga)|Dragon Ball]]'' sequel", "Dragon Ball Z"), (None, None))
eq("lead: no title -> first link (old behaviour)",
   MT.lead_link("''[[Weekly Shōnen Jump]]'' first", None), ("Weekly Shōnen Jump", "Weekly Shōnen Jump"))

# ---- main-article infobox --------------------------------------------
IB = """{{Infobox animanga/Header
| name = Test Work
| ja_kanji = テスト
| ja_romaji = Tesuto
| image = Test Work vol 1.jpg
| genre = {{ubl|[[Dark fantasy]]<ref>x</ref>|[[Adventure fiction|Adventure]]}}
}}
{{Infobox animanga/Print
| type = manga
| author = [[Kentaro Miura]]
| illustrator = [[Some Artist|Artist]]<br>[[Another]]
| imprint = [[Jets Comics]]
| publisher = {{ubl|[[Shueisha]]|[[Shueisha]] (bunko)}}
| publisher_en = {{English manga publisher|NA=[[Viz Media]]|UK=Viz Media}}
| demographic = [[Seinen manga|Seinen]]
| magazine = [[Weekly Young Jump]]
| first = September 8, 2011
| last = September 18, 2014
| volumes = 14
}}"""
mf = MA.parse_main(IB)
eq("infobox status ended", mf.get("status"), "ended")
eq("infobox first/last", (mf.get("first"), mf.get("last")), ("2011-09-08", "2014-09-18"))
eq("infobox volumes", mf.get("volumes"), 14)
eq("infobox publisher (first of list)", mf.get("publisher"), "Shueisha")
eq("infobox publisher_en (NA, unlinked)", mf.get("publisher_en"), "Viz Media")
for raw, want in [
    ("Tokyopop (former)<br />J-Novel Club", "J-Novel Club"),
    ("Tokyopop <small>(former)</small>", "Tokyopop"),
    ("Eclipse Comics (former)<br>Dark Horse (current)", "Dark Horse"),
    ("Kadokawa Shoten <small>(vol. 1-2)</small><br />Media Factory <small>(vol. 3-present)</small>", "Media Factory (vol. 3-present)"),
    ("Type-Moon <small>(original creator)</small><br>Kodansha <small>(commercial publisher)</small>", "Kodansha (commercial publisher)"),
    ("Coolmic (digital)<br>Seven Seas Entertainment (print)", "Seven Seas Entertainment (print)"),
    ("Moonlight Novels<br>(Shōsetsuka ni Narō)", "Moonlight Novels (Shōsetsuka ni Narō)"),
    ("Yen Press<br />Sol Press <small>(formerly)</small>", "Yen Press"),
    ("[[Viz Media]]}}<br>{{English manga publisher", "Viz Media"),
    ("Toyspress (former)}} [[Titan Publishing Group#Titan Manga", "Titan Manga"),
    ("[[Kodansha USA|Kodansha Comics]]<br>Tokyopop (former)", "Kodansha Comics"),
    ("Sun Magazine<br/>Ichijinsha<br>Futabasha", "Sun Magazine"),
    ("{{ubl|[[Shueisha]]|[[Shueisha]] (bunko)}}", "Shueisha"),
    ("", ""),
]:
    eq(f"publisher field {raw[:40]!r}", MA._publisher_field(raw), want)
eq("infobox demographic", mf.get("demographic"), "Seinen")
eq("infobox header name", mf.get("name"), "Test Work")
eq("infobox kanji/romaji", (mf.get("ja_kanji"), mf.get("ja_romaji")), ("テスト", "Tesuto"))
eq("infobox genres (list template, refs stripped)", mf.get("genre"), ["Dark fantasy", "Adventure"])
eq("infobox genres (plain list with a cite ref)", MA.parse_main(IB.replace("| genre = {{ubl|[[Dark fantasy]]<ref>x</ref>|[[Adventure fiction|Adventure]]}}",
   "| genre = [[Action fiction|Action]], [[Adventure]]<ref>{{cite web|url=https://www.madman.com.au/catalogue/view/19467|title=x|access-date=2018}}</ref>")).get("genre"), ["Action", "Adventure"])
eq("infobox genres (editor comment dropped)", MA.parse_main(IB.replace("| genre = {{ubl|[[Dark fantasy]]<ref>x</ref>|[[Adventure fiction|Adventure]]}}",
   "| genre = <!-- Note: Use\ncite reliable sources to identify genre -->{{ubl|[[Adventure fiction|Adventure]]|[[Fantasy]]}}")).get("genre"), ["Adventure", "Fantasy"])
TWO = IB + """
{{Infobox animanga/Print
| type = light novel
| volume_list = List of Test Work light novels
| first = 2009-04-10
| volumes = 29
| publisher = ASCII Media Works
}}"""
eq("infobox: the block whose volume_list is our list article wins", MA.parse_main(TWO, list_article="List of Test Work light novels").get("status"), "ongoing")
eq("infobox: without a list article the first block is used", MA.parse_main(TWO).get("status"), "ended")
eq("infobox author", mf.get("author"), ["Kentaro Miura"])
eq("infobox illustrators (<br> list)", mf.get("illustrator"), ["Artist", "Another"])
eq("infobox imprint", mf.get("imprint"), "Jets Comics")
eq("infobox image file", mf.get("image"), "Test Work vol 1.jpg")
import relations as REL
BN = {REL.norm(t): t for t in ("Attack on Titan", "Dragon Ball", "Bleach", "Blue", "Re:Zero")}
eq("relation: subtitle spin-off", REL.parent_of("Attack on Titan: Before the Fall", BN), ("Attack on Titan", "spin_off"))
eq("relation: Z sequel", REL.parent_of("Dragon Ball Z", BN), ("Dragon Ball", "sequel"))
eq("relation: Super sequel", REL.parent_of("Dragon Ball Super", BN), ("Dragon Ball", "sequel"))
eq("relation: same title is not its own parent", REL.parent_of("Bleach", BN), (None, None))
eq("relation: short parent ignored", REL.parent_of("Blue Lock", BN), (None, None))
eq("relation: plain word continuation is not a relation", REL.parent_of("Bleached Bones", BN), (None, None))
eq("publisher_en: lower-case, empty template -> none", MA._publisher_en("{{english manga publisher|NA=}}"), None)
eq("publisher_en: positional (One Piece shape)",
   MA._publisher_en("{{English manga publishers\n| AUS = [[Madman Entertainment]]\n| [[Northern America|NA]]/[[United Kingdom|UK]]|[[Viz Media]]\n}}"), "Viz Media")
eq("infobox ongoing when no last", MA.parse_main(IB.replace("| last = September 18, 2014\n", "")).get("status"), "ongoing")

# ---- line corrections ------------------------------------------------
import importlib.util, sqlite3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("corrections", os.path.join(ROOT, "tier2", "corrections.py"))
corr = importlib.util.module_from_spec(spec); spec.loader.exec_module(corr)
cdb = sqlite3.connect(":memory:")
cdb.executescript(open(os.path.join(ROOT, "schema", "schema.sql"), encoding="utf8").read())
cdb.execute("INSERT INTO work(id,primary_title,created_at,updated_at) VALUES('w_t','Test Work','x','x')")
cdb.execute("INSERT INTO release_line(id,work_id,medium,market,language,created_at,updated_at)"
            " VALUES('rl_jp','w_t','manga','JP','ja','x','x')")
LINE = {"work": "w_t", "market": "EN", "medium": "manga", "name": "Test Work", "publisher": "Seven Seas",
        "volumes": [{"number": "1", "isbn13": "978-1-64505-000-1", "release_date": "2020-10-06", "contains": [1]},
                    {"number": "2", "isbn13": "9798888779347", "release_date": "2021-02", "page_count": 180, "contains": [2, 3]}],
        "source_url": "https://example.test/series", "checked": "2026-09-04"}
corr.apply_line_corrections(cdb, entries=[LINE], verbose=False)
rid = corr._id("rl_", "w_t", "manga", "EN", "Test Work")
eq("line corr: line row", cdb.execute("SELECT market, language, publisher FROM release_line WHERE id=?", (rid,)).fetchone(),
   ("EN", "en", "Seven Seas"))
eq("line corr: name claim", cdb.execute("SELECT value, source FROM claim WHERE entity='release_line' AND entity_id=? AND field='line_name'", (rid,)).fetchone(),
   ("Test Work", "correction"))
eq("line corr: volumes", cdb.execute("SELECT number, isbn13, release_date, release_date_precision, page_count FROM volume WHERE release_line_id=? ORDER BY number", (rid,)).fetchall(),
   [("1", "9781645050001", "2020-10-06", "day", None), ("2", "9798888779347", "2021-02", "month", 180)])
eq("line corr: overrides + claims per value",
   (cdb.execute("SELECT COUNT(*) FROM override WHERE entity='volume'").fetchone()[0],
    cdb.execute("SELECT COUNT(*) FROM claim WHERE entity='volume' AND source='correction'").fetchone()[0]), (5, 5))
eq("line corr: composition points at the JP line",
   cdb.execute("SELECT ref_list, ref_line_id FROM composition c JOIN volume v ON v.id=c.volume_id WHERE v.release_line_id=? ORDER BY v.number", (rid,)).fetchall(),
   [("[1]", "rl_jp"), ("[2, 3]", "rl_jp")])
corr.apply_line_corrections(cdb, entries=[LINE], verbose=False)
eq("line corr: idempotent", cdb.execute("SELECT COUNT(*) FROM volume").fetchone()[0], 2)
try:
    corr.apply_line_corrections(cdb, entries=[dict(LINE, name="Other", volumes=[{"number": "1", "isbn13": "9784063842760"}])], verbose=False)
    eq("line corr: JP ISBN on an EN line rejected", "no error", "ValueError")
except ValueError:
    eq("line corr: JP ISBN on an EN line rejected", True, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all parser tests passed")
