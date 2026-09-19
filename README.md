# OpenTome

*A multi-market volume catalogue for manga, light novels, manhwa and webtoons.*

Per-volume metadata across **Japanese, English, French and German** release lines, with
**day-precision release dates**, ISBNs, and chapter-to-volume composition.

The name is deliberate: *tome* means volume natively in English, French, Spanish,
Italian and Portuguese — five of the target markets, no translation needed — and says
nothing about manga, so light novels, manhwa and manhua are not second-class citizens in
the catalogue's own name.

## Why

Every existing source — AniList, MangaUpdates, MangaDex, ComicVine, and the tools built
on them (Komf, Teemii, Omnibus, Mylar3) — is **series-level or chapter-level, and
English-title-centric**. None answer:

> *Which printed volume is this, in which market, when did it ship, what is its ISBN,
> and which canonical chapters does it contain?*

France is the world's #2 manga market (36M volumes, €309M in 2025). A French reader with
a Komga library of Glénat and Ki-oon editions currently has **no tool at all**.

## Download

The catalogue is one SQLite file, published as a GitHub release:

- **Current build:** <https://github.com/itsnickspiro/mangarr-metadata/releases/tag/metadata>
  — `manga-metadata.sqlite` (the catalogue) and `version.json` (its label, sha256 and
  size).
- Direct links: [`manga-metadata.sqlite`](https://github.com/itsnickspiro/mangarr-metadata/releases/download/metadata/manga-metadata.sqlite)
  · [`version.json`](https://github.com/itsnickspiro/mangarr-metadata/releases/download/metadata/version.json)
- Every published build also has its own release, tagged `opentome-YYYY-MM-DD`, so any
  earlier build can be fetched or rolled back to.

Mangarr fetches this automatically: it polls `version.json`, verifies the sha256, and
swaps the new file in. Nothing to configure.

The file's `meta` table carries the build label (`gcd_dump` — a legacy key name kept for
the updater's contract; the catalogue contains no GCD data), the licence and the
attribution block. The schema is documented in `docs/schema-v1.md`; the ids in
`docs/id-scheme.md`.

Site: [opentomedb.com](https://opentomedb.com) (coming).

## Licence

- **Code:** MIT — [`LICENSE`](LICENSE).
- **Data:** free for non-commercial use with attribution — **CC BY-NC 4.0** as far as
  the compilation is ours, with each source's own terms controlling the fields that
  came from it — [`LICENSE-DATA.md`](LICENSE-DATA.md).

Data is sourced so the catalogue stays as unencumbered as possible: **BnF** (Etalab
Open Licence), **DNB** (CC0; the German primary source, not yet wired in — see
`docs/german-market.md`), **Wikipedia** as a *citation index* — facts only, never
prose, every value attributed to the primary source the article itself cites —
**openBD** and **Open Library** (both non-commercial by their terms, which is why the
full build is). **Google Books, MangaDex and Rakuten** forbid database-building
and/or commercial use and are not used. Every claim carries a `licence`; the verified
record of each source's terms is [`docs/legal-position.md`](docs/legal-position.md).

## Contribute

Corrections are **data, not a website**: pull requests against the three JSON files in
[`corrections/`](corrections/), one entry per fact, each with the `source_url` that was
actually checked and a `checked` date. A check runs on every pull request; a merged
correction lands in the next build and is asserted by the artifact's contract test so it
cannot quietly disappear. For something you have noticed but not checked, open an issue
with one of the templates (*Wrong fact*, *Missing volume*, *New release line*, *Merge
lines*). [`CONTRIBUTING.md`](CONTRIBUTING.md) has the steps.

There is also a [Discord](https://discord.gg/bQVwv54KdP): `#corrections` is a forum (one thread per wrong or missing fact, with the source you checked), `#announcements` carries each publish, `#general` is for questions. Feature requests belong in [GitHub issues](https://github.com/itsnickspiro/opentome/issues/new/choose) so they are not lost in chat.

## How the catalogue is built

`tier0/rebuild_all.sh` runs the whole pipeline, in order:

1. **Unit tests** — a rebuild on a broken parser is worse than no rebuild.
2. **Work identity** — Wikipedia language links become cross-language work classes.
3. **Corpus** — volume lists from the English, French and German Wikipedias, plus the
   main article's titles, status, publishers and relations.
4. **Enrichment** — every ISBN checked against openBD (JP), Open Library (EN, FR) and
   BnF (FR), each value stored as a claim with its source and licence.
5. **Clean** — date semantics (`_precision`, `_type`), malformed ISBNs.
6. **Corrections** — `corrections/*.json` applied, ranked above every source.
7. **Resolve** — claims become values with a confidence; disagreements are recorded,
   not averaged.
8. **Audit** — exits non-zero on any defect.
9. **Export** — the consumer-shaped `manga-metadata.sqlite`, with AniList ids for the
   English lines and ids carried forward from the previous build.
10. **Gates** — the artifact contract test, then a replay of a real library's series
    matching against the committed fixture (`export/fixtures/library.json`); a coverage
    failure fails the build before the new artifact replaces the old one.

Every source response is cached under `.cache/` (never committed — it is raw third-party
data), so a re-run costs minutes and zero requests; a cold build re-fetches everything
under each source's rate limit and takes hours.

The build runs in GitHub Actions on a weekly schedule and on demand. A scheduled run
builds and gates; **publishing is a separate, human decision** — a dispatched run with
`publish` set, which runs `export/publish.sh`: it refuses any artifact whose label is not
`opentome-YYYY-MM-DD` or whose aliases came from anywhere but this pipeline, creates the
per-build release, and re-points `metadata` at it.

```
tier0/          Wikipedia extraction (one dialect entry per wiki language), work identity, the rebuild script
tier1/          enrichment and cross-verification against independent sources
tier2/          clean, corrections, resolve (claims -> values + confidence), audit
schema/         schema.sql + loader, per-field provenance
export/         the consumer artifact, AniList ids, contract test, measure gate, publish
corrections/    hand-checked facts, as data
docs/           findings -- every design decision traces to one
```

## Docs

| Doc | What it establishes |
|---|---|
| `docs/step0-findings.md` | Source licensing and feasibility; what is and isn't usable |
| `docs/legal-position.md` | Each source's terms, verified; what a free release and a commercial one can carry |
| `docs/schema-v1.md` | Schema design, traced to findings |
| `docs/id-scheme.md` | The id scheme — the public contract |
| `docs/smoke-test-library16.md` | 536 volumes extracted; the silent-mismatch bug and its fix |
| `docs/tier1-crossverify.md` | 0.24% true error rate; the week-multiple calibration rule |
| `docs/calibration.md` | The week-multiple rule derived and independently replicated |
| `docs/verification-coverage.md` | What "verified" means, field by field |
| `docs/corpus-v1.md` | The first full build: 5,823 articles, what it produced |
| `docs/cleanup-v1.md` | What the audit found and what fixing it cost |
| `docs/cleanup-v2.md` | What aggregate match counts were hiding; line-level measurement |
| `docs/german-market.md` | The German market measured: the extractor works, the articles are empty; DNB becomes the German primary source |
| `docs/mangarr-migration.md` | Why Mangarr is the catalogue's home, and how it was migrated in |

## Identifiers

OpenTome ids are a **public contract** — see `docs/id-scheme.md`. Consumers store them as
`tome_id`, alongside `tvdb_id` / `tmdb_id` / `anilist_id`. They are never reused, never
re-keyed, and a retired id resolves forever rather than 404ing.

`series.anilist_id` in the exported artifact is filled by `export/resolve_anilist.py`
(pipeline step 8a): AniList's id for the English line, chosen with the ranking Mangarr
itself applies — no one-shots, primary title over synonym (and never a synonym-only carrier
while a primary-title candidate is on the page, even a rejected one), a one-sided volume-count
check (a candidate with far fewer volumes than the line, or more than 4× for lines of 3+
volumes, is junk; more within 4× is a 2-in-1 English edition; a 1–2 volume line has no
ceiling against its own name, though an alias retry always keeps it), popularity on a tie,
the de-slugged name, then every alias ranked against the page the name search fetched and a
fresh search for the first three that miss it — cached under `.cache/anilist/` so a
rebuild costs no requests for names already seen. A line the rules cannot place stays
NULL; it is never guessed, because Mangarr pins whatever the catalogue says. Measured on
`opentome-2026-09-04`: 2,614 of 3,053 English lines bound, 87.8 % of those with 3+ volumes
(the contract allows up to 15 % unresolved; the residue is list-article and edition names).
