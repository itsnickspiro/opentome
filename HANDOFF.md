# HANDOFF — OpenTome

_Last updated: 2026-09-18_

## What this is now

OpenTome is a CI-built catalogue. The `catalogue` workflow runs the whole pipeline
(`tier0/rebuild_all.sh`) every Sunday and on demand: every unit test, the audit, the
artifact contract test and the measure gate, on a warm source cache. A scheduled run
builds and gates and stores the artifact as a run artifact — it never publishes.
Publishing is a human decision: someone presses *Run workflow* with `publish = true`,
and `export/publish.sh` creates the release `opentome-YYYY-MM-DD` on
`itsnickspiro/mangarr-metadata` and re-points the `metadata` alias at it. Every Mangarr
install polls that alias's `version.json`, verifies the sha256 and swaps the new
catalogue in — nothing to configure on the user side.

The repository carries no browser and no editing site. Corrections arrive as pull requests against
`corrections/*.json` (see `corrections/README.md`, `CONTRIBUTING.md`); a check runs on
every PR. Issues use the four templates under `.github/ISSUE_TEMPLATE/`.

## State of play

Last measured build: `opentome-2026-09-18` (the artifact's `meta.gcd_dump`).

- Artifact `manga-metadata.sqlite`: 11,657 series (release lines), 112,083 volumes
  + 938 `volumes_special`; 78,956 aliases; corrections applied: 0 (all three files are
  still `[]`).
- Measure gate (stage 8d, replaying Mangarr's series pick over the committed
  `export/fixtures/library.json`): matched 48/49, coverage failures 0. The one miss is
  *Mushoku Tensei: Roxy Gets Serious*, a spin-off line the catalogue does not carry yet.
- AniList ids: 42/48 picked lines carry one; 2,214 of the 2,522 English lines with
  three or more volumes are bound (the README's 2,614 / 3,053 figure counts every
  English line, not only those with 3+ volumes).
- Every source response is cached under `.cache/` (never committed); the CI cache is
  seeded once by the `seed-cache` workflow and kept warm by `cache-keepalive`.

## Gates

Every one of these exits non-zero and stops the build; none is optional.

- **Stage 0 — unit tests:** `tier0/test_parser.py`, `tier2/test_resolve.py`,
  `export/test_resolve_anilist.py`, `export/test_measure_fixture.py`.
- **Stage 7 — audit:** `tier2/audit.py` reports remaining defects and fails on any.
- **Stage 8c — artifact contract:** `export/test_artifact.py` asserts the consumer's
  schema, the id contract and every committed correction.
- **Stage 8d — measure gate:** `export/measure_library.py` replays a real library's
  series matching against `export/fixtures/library.json`; a coverage failure (an owned
  volume the picked line lacks) fails the build before the new artifact replaces the old.

`export/publish.sh` adds two refusals of its own: the label must be `opentome-YYYY-MM-DD`
and `meta.alias_provenance` must be `opentome` — the clean-room guard.

### 2026-09-19 — publisher hygiene, Discord announce
- `tier0/main_articles._publisher_field`: one clean publisher from a `<br>`/`<small>`/residue-glued infobox field (prefers the entry that is not former/expired/revoked, then one marked current/present/print, else the first); used for `publisher` and every `publisher_en` value. Contract rule `publishers with markup` in `export/test_artifact.py`. CI build 35453130837: `publishers with markup: 0` (was 113), measure gate `matched 48/49, 0 coverage failures`. **Not published yet — the next `publish=true` dispatch ships it** (Nick's gate).
- `catalogue.yml`: on a publish, an embed goes to the Mangarr Discord `#catalogue` (secret `DISCORD_CATALOGUE_WEBHOOK`; the step is skipped when the secret is unset).
- Looked at and left: 3,947 lines with blank status (1,530 main lines whose work has no Wikipedia status, 1,558 sub-lines that deliberately do not inherit the work's status) and 53 lines with zero volumes (real works without a volume table; 13 are sub-lines that could be folded into their parent via `id_redirect` — not done, no consumer needs it).

## Next

Phase 3 is done (2026-09-18). What runs where now:

- **Build + publish:** GitHub Actions in this repository. `catalogue` runs every Sunday
  09:00 UTC (build + every gate, never publishes); a maintainer publishes by dispatching it
  with `publish = true`. First CI build: run 35387125298 (identical figures to the last
  workstation build, ids carried exactly); first CI publish: run 35393862685 →
  `opentome-2026-09-18` on `itsnickspiro/mangarr-metadata`, alias `metadata` re-pointed,
  picked up by Mangarr the same hour.
- **Cache:** the Actions cache, seeded from the private `opentome-cache` repo's `seed`
  release (run 35387058848), kept warm by `cache-keepalive`.
- **Site:** `pages` builds `site/` on push, on every `catalogue` completion and on a
  Sunday schedule; live at the GitHub Pages URL, moving to opentomedb.com once DNS
  resolves (custom domain is set; HTTPS enforcement follows the certificate).
- **Corrections:** issues and pull requests here; `corrections check` runs on every PR.
  The first one through the path — #1, the English light-novel line of Mushoku Tensei
  (Seven Seas, 26 volumes, every ISBN and date from an Open Library edition record) — is
  merged and in the next build (`rl_250d21561d35`); it reaches Mangarr on the next publish.
  It also found a gate bug: the artifact contract's "multi-entry composition" heuristic
  (`LENGTH > 3`) refused `[10]`; the export now carries composition only for omnibus lines
  and the rule checks the real invariant.
- **Retired:** the old browser and the workstation rebuild agent — nothing OpenTome-related
  runs outside CI. The pre-CI history stays on the maintainer's private mirror.

Open, in order of value:

1. **Publish again** so the Mushoku Tensei line reaches consumers (a maintainer's dispatch).
2. The `metadata` alias release's notes on `mangarr-metadata` still describe the pre-OpenTome
   (GCD-era) artifact; they should be rewritten to the OpenTome text (maintainer decision —
   it changes existing release content).
3. Known data defects surfaced by the site: 113 `series.publisher` values carry Wikipedia
   infobox markup (`<br>`, `<small>`); 53 lines have `volume_count = 0`; `status` is blank on
   3,946 lines; `country` is empty everywhere; `mangaupdates_id`/`mangadex_id` are unfilled.
4. DNB as the German primary source (`docs/german-market.md`) — today's German lines come
   from Wikipedia only; the site says so.
5. The multi-market validation spike; per-line pages on the site (v2).

## Gotchas for contributors

- A `catalogue` run fails at its cache step (`fail-on-cache-miss`) until `seed-cache` has run once on `main`; that is deliberate — a cold build would spend hours of polite-rate requests and be killed by the 180-minute limit.
- **Never pipe a gate's exit away.** `set -o pipefail` is on in `rebuild_all.sh`; a
  `| tee` or `| head` around a gate outside it hides the failure the gate exists to raise.
- **`gcd_dump` is a legacy key name.** It labels the build (`opentome-YYYY-MM-DD`) and
  is kept for the updater's contract; the catalogue contains no GCD data.
- **Ids are a public contract.** `tome_id` / `tome_work_id` are never reused or
  re-keyed; the export carries integer ids forward through `id_map` from the last
  published artifact (CI downloads it before the rebuild). A merge writes `id_redirect`.
- **Corrections must pass `python3 tier2/corrections.py --check`** — well-formed,
  `source_url` and `checked` present, every key resolving against a published artifact
  (`--artifact <download>`; the default is the last local build). The PR check runs
  exactly that against the `metadata` release.
- **`.cache/` is never committed.** It is raw third-party responses whose redistribution
  the sources' terms do not allow. A cold build re-fetches everything under each
  source's rate limit (Wikipedia 429s without the 1.1 s throttle; put a real contact in
  the User-Agent) and takes hours.
- **Scheduled workflows on a public repository pause after 60 days without commits.**
  A merged PR resets the clock; a paused `catalogue` schedule shows on the Actions tab.

The pre-CI engineering history (2026-08 → 2026-09) is preserved on the maintainer's
private mirror.
