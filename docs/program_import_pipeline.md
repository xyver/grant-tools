# Program Import Pipeline

The repeatable process for taking a grant program from "found it" to "the MCP
uses it correctly". Federal and state follow the same stages; only the sources
differ.

Owner of: the end-to-end import process and the MCP-readiness contract. The
field-level rules are enforced by `validate_program.py`, which is the
executable version of this document - when the two disagree, the validator
wins, because it cannot go stale.

Read alongside:
- [grant_collectors.md](grant_collectors.md) - curated CSV field definitions
- [proposal_section_model.md](proposal_section_model.md) - section lists and
  the standard for what a "complete" program row carries
- [research/section_list_findings.md](research/section_list_findings.md) -
  access_path and lifecycle_status, and what is already researched

---

## Priority: currently-live first

The MCP is a pre-submission tool. Its job is to point a user at grants they can
apply to now. So the import order is:

1. **Open programs** an applicant can pursue today.
2. **Recurring programs** between rounds - real next-cycle targets.
3. **History around programs we already carry** - closed rounds of live
   programs, as writing precedent. This is enrichment of existing rows, not new
   imports.

Do not bulk-import the historical archive as programs. 81,000 closed
Grants.gov opportunities are precedent, reachable per-CFDA on demand; they are
not 81,000 catalog rows.

---

## Sources

### Federal - Grants.gov search2 API

One API covers the whole federal system. `https://api.grants.gov/v1/api/search2`,
POST JSON. Scale as of 2026-07: ~1,250 posted, ~500 forecasted, ~81,000 closed.

Useful filters:

- `oppStatuses`: `posted` | `forecasted` | `closed|archived`
- `eligibilities`: `12` is Nonprofits with 501c3 status
- `fundingCategories`: `ENV`, `NR`, `DPR`, `AG`, `CD`, `EN`, `ELT`, ...
- `cfda`: exact CFDA lookup - the join key to our catalog
- `keyword`: free text

Every opportunity carries `cfdaList`, which is how a candidate maps to a
program row and unlocks precedent + lifecycle.

**Category filters over-collect.** "Environment + nonprofit" returns hundreds
of NIH biomedical awards. Combine a category with domain keywords, and expect
to discard most hits at the vetting stage. The API is a candidate firehose, not
a curated list.

### State - per-portal, no central API

State programs (CAL FIRE, CEC, VIPC, Strategic Growth Council, Caltrans) each
publish on their own site and have no CFDA. There is no equivalent to
Grants.gov. Each state is its own source; the research and import stages below
are identical, but discovery is manual and lifecycle cannot be derived from
Grants.gov.

---

## The stages

### 1. Discover

Pull candidates from the source. For federal, filter to open + nonprofit-
eligible + domain keywords, dedupe by CFDA.

### 2. Vet - is this in scope at all

Before spending research time, confirm the candidate is:

- **relevant** to the vertical, not a keyword collision (an NIH R01 caught by
  "recovery" is not a disaster program)
- **applicable** - an organization can actually apply. Many federal listings
  are formula allocations, direct-to-household, or congressional earmarks with
  no competition. That becomes `access_path`, but catch it early - it changes
  whether the row is worth building.

A candidate that fails vetting is still worth one line in the research notes so
nobody re-evaluates it later.

### 3. Research the row

Fill every field. The lessons that make or break MCP usage:

| Field | The rule, and why |
|---|---|
| `eligible_applicant_types` | EXACT vocabulary. `nonprofit_501c3`, not `nonprofit` - the matcher is an exact-string gate and a near-miss matches nothing, silently. Reuse existing tokens; a brand-new one needs a matching rule. |
| `cfda_number` | The master key. Federal programs without it get no precedent and no lifecycle. Map it from the award data or the Grants.gov listing. |
| `geography_scope` | Must have a backend matcher. Adding a new scope means adding a `GEOGRAPHY_STATE_MAP` entry first, or matches degrade to "unknown" silently. |
| `sector_tags` | From the existing vocabulary. Matching is an OR, so a typo'd tag simply never matches. |
| `requires_*` tri-states | `UNKNOWN` when unconfirmed - never guess `FALSE`. An unknown requirement is surfaced, not silently dropped. |
| `access_path` + `access_notes` | How the program is actually reached. Eligibility says who qualifies; this says whether they can submit at all. |
| `source_url` | Specific enough to re-verify. A NOFO PDF beats a homepage. Without it, nothing from the row can be attributed. |

Plus a section list where one is published - see
[proposal_section_model.md](proposal_section_model.md). Never fabricate one;
`found: false` is a valid result.

### 4. Validate

```powershell
python validate_program.py <program_id>
```

ERROR must be clear before the row is MCP-ready. WARN is a quality signal
(usually a singleton applicant type or tag - a typo, or a real new value that
needs a matching rule). INFO is coverage, not a defect.

Run with no argument to check the whole catalog; `--strict` fails on WARN too.

### 5. Enrich from live sources

These are re-runnable and keep the catalog current without re-researching:

```powershell
# lifecycle_status (open / recurring / historical) from Grants.gov per CFDA
python .../build_lifecycle_from_grantsgov.py --write

# access_path / access_notes from researched NOFO text
python .../build_access_paths.py --write

# section-list template rows from researched output
python .../build_section_templates.py --write
```

Precedent (past winners, award sizes, recipient types) needs no import step -
`get_grant_precedents()` reads it live from the award data for any program with
a CFDA. That is how history accrues around a live program: add the program,
and its precedent is already there.

### 6. Merge and confirm

Rebuild any derived rows, run the validator over the full catalog, run the test
suites, and confirm coverage moved. Commit the CSV and any regenerated
artifacts together.

---

## Keeping it current

`lifecycle_status = open` is a point-in-time snapshot. Deadlines pass. Re-run
`build_lifecycle_from_grantsgov.py` on a schedule so "open now" stays true;
this is the same periodic-snapshot pattern as the existing open-opportunities
feed, not a one-time load.

---

## What "properly organized for MCP usage" means, in one line

A program row is MCP-ready when: an applicant of the right type is matched by
exact vocabulary, its geography is verifiable, its access path and lifecycle
are known, its requirements are attributed to a source, and - where published -
its section list is recorded and agency-verified. `validate_program.py` is the
enforcement of that sentence.
