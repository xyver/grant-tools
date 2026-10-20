# Proposal Section Model

How the grant MCP reads a proposal as structured sections, decides what each
funder requires, and turns that into specific feedback.

Owner of: the universal section vocabulary, per-program requirement
resolution, extraction behavior, and the standard every new program row must
meet. Implementation lives in `proposal_sections.py`.

---

## The problem

Two failures this exists to prevent.

**Prose is not addressable.** A brief arrives as a document. Reviewing or
editing it means reading to find "the budget part". Nothing can be checked,
scored, or diffed until the document is mapped onto named sections.

**Vague feedback is useless.** "This proposal needs work" tells an applicant
nothing. "No measurable outcomes stated, and FEMA BRIC scores on them" tells
them exactly what to write next. The second requires knowing both what the
document contains and what the specific funder asks for.

---

## Two layers: universal set, specific subset

There is one **universal set** of sections a proposal could need. No funder
wants all of them. Each funder requires a **subset**, and different funders
require different subsets.

```
universal vocabulary  (18 sections, proposal_sections.PROPOSAL_SECTIONS)
        |
        +-- agency-published list      <- authoritative where it exists
        +-- funder_type default        <- floor everywhere else
```

The universal set is the floor, not the requirement. A federal construction
program and a small family foundation share maybe half of it.

### The universal vocabulary

| Section | Why funders ask |
|---|---|
| `title` | Identifies the project on every form |
| `applicant_identity` | The entity of record; drives eligibility |
| `contact` | A named human with role, phone, email |
| `summary` | Often the only section every reviewer reads |
| `need_statement` | Why the work is necessary, with evidence |
| `location` | Place of performance; drives geographic eligibility |
| `activities` | What gets done, in what order, by whom |
| `partners` | Roles and commitments; some programs require a co-applicant |
| `outcomes` | Measurable results - what reviewers actually score |
| `evaluation` | How success gets verified |
| `timeline` | Must fit the program's allowed project length |
| `budget` | Unit costs and assumptions, not just a total |
| `funding_request` | The specific ask |
| `match_funding` | Cost share; unaddressed match is a hard blocker |
| `capacity` | Evidence the applicant can execute an award this size |
| `sustainability` | What happens when the grant period ends |
| `compliance_registration` | SAM.gov/UEI; gates federal submission, takes weeks |
| `target_funder` | Which door this is aimed at, so fit can be checked |

Add to this list only when a real program requires something none of these
cover. It is a shared vocabulary; growing it casually makes cross-program
comparison meaningless.

---

## Requirement resolution and provenance

For any program, requirements resolve in this order:

1. **Agency-published section list** (`grant_writing_templates.csv`,
   `agency_verified=TRUE`). This governs. Never override it.
2. **Agency-inferred list** (`agency_verified=FALSE`). Use it, label it as
   inferred, and tell the user to check the current NOFO.
3. **Universal set + funder_type default.** Everywhere else.

`resolve_requirements()` returns the `source` for exactly this reason. **Never
present an inferred list as if the agency published it.** An applicant who
writes to a fabricated section list and gets rejected has been actively
harmed by the tool.

### Current coverage, and why this matters

As of 2026-07-21: **65 of 127 programs have a section list** (up from 10).
Every researched row is agency-verified. The other 62 fall through to the
funder_type default.

Status and findings are recorded in the program catalog and template CSVs.

That research surfaced something more important than section names: **eight
catalog programs have no direct application path for any organization**
(FEMA Public Assistance is post-disaster reimbursement, USDA Section 504 is
direct-to-household, ARC Local Access Roads is submittable only by a state ARC
Alternate), and **six more are invitation-only, LOI-gated, or state-routed**.

The tool has been scoring those as strong matches with no signal that the
applicant cannot submit. Eligibility tables cannot express this - the applicant
type is often genuinely eligible; the submission route is what blocks them. The
recommended fix is an access-path field on the program row
(`direct`, `state_routed`, `invitation_only`, `loi_required`,
`not_an_application`) surfaced in the shortlist.

### funder_type defaults

Derived from catalog columns already present, not a hand-maintained list:

| funder_type | Adds to the core set |
|---|---|
| `federal` | compliance_registration, timeline, evaluation, capacity, partners |
| `state` | timeline, evaluation, capacity |
| `private_foundation` | sustainability, capacity, contact |
| `defense_international` | compliance_registration, timeline, evaluation, capacity |

Plus, from the tri-state flags:

- `requires_match_funding=TRUE` adds `match_funding`
- `requires_government_intermediary=TRUE` adds `partners`

---

## Extraction is heuristic, and says so

`extract_sections()` matches headings and keywords and returns `present`,
`thin`, or `missing` **with the evidence it used**. It is a reviewer's
checklist, not comprehension of the proposal.

`thin` means the section was mentioned but has little supporting text - a
passing reference rather than a section. That distinction is most of the
value: "you mention partners in one line" is different from "you have no
partners section".

### Extraction traps already hit

Real lessons from running this against an actual brief. Both produced
confidently wrong output:

- **Plurals.** `\brequest\b` does not match "Funding Requests". A document
  with a literal `Funding Requests` heading was reported as having no funding
  request. Heading patterns must be plural-tolerant.
- **Unlabelled titles.** Documents rarely contain the word "Title"; the first
  substantive line is the title. Requiring the label reported every real
  document as untitled.
- **Heading collisions.** "Collaboration" is a work-plan heading, not a
  partners heading, in most briefs. One section's pattern silently claiming
  another's content is the most likely failure mode here.

When adding a pattern, test it against a real document, not an invented one.
A checklist that reports a present section as missing is worse than no
checklist, because it sends the author to rewrite something they already did.

---

## Related phase 1 analysis

Two behaviors that use the same "read what the document actually says" idea:

**Declared targets.** A brief usually already names the funders it is aimed
at. `_extract_declared_funders()` pulls them from the narrative and reports,
per funder, which programs the applicant is eligible for and which are
eligible but missed on sector fit, with the tags that would close the gap.
Discovery that ignores the named targets answers a question the author did
not ask.

**Alternative framings.** Sector matching is an OR across tags, so scoring
every tag at once returns one blended ranking dominated by whichever tag has
the most programs. Scoring one tag at a time shows what each pitch reaches -
the same debris-removal work framed as `workforce` surfaces ARC WORC, framed
as `economic_development` surfaces ARC READY, and neither appears in the
blended list. This is the "what else could this same proposal be" question.

---

## Standard for new program rows

Every program row added from here on must carry enough for the proposal path
to say something specific. A row that only supports "you are eligible" is
half a row.

Required, in addition to the fields in
[grant_collectors.md](grant_collectors.md) "Curated CSV workflow":

1. **A section list, or an explicit statement that none was found.** Add a
   `grant_writing_templates.csv` row with `applies_to_program_ids`. Set
   `agency_verified=TRUE` only when the sections come from the agency's own
   NOFO, application guide, or form - not from a blog, a consultant, or
   inference from a sibling program.
2. **The source URL for that section list**, specific enough that the next
   person can re-verify it. A NOFO PDF link beats a program homepage.
3. **At least one real example**, recorded per the standard below.
4. **Page limits and required forms** where published - these change what
   "complete" means as much as the section list does.

If the agency publishes nothing, say so in `notes` and let the row fall
through to the funder_type default. An honest gap is fine. A fabricated
section list is not.

### What counts as an example

An example is a concrete artifact from the real program, not a description of
one. In descending order of value:

1. A published NOFO or application guide listing required sections
2. A funded abstract or award description from that program
3. An agency-published sample or template application
4. A scored review rubric or evaluation criteria list

Record where it came from and when it was checked. Award descriptions are
already reachable through the USAspending pull for any program with a CFDA
number, which is the cheapest source of real examples we have - see the ARC
mapping work for how much a CFDA unlocks.

Do not paste applicant proposal text of unknown provenance into this repo.
Link to the public source instead.

---

## Research task: the remaining uncovered programs

Standing work. Check existing program catalog and template rows before
researching a program again.

Two method notes that cost real time to learn:

- **Batch by funder, not arbitrarily.** One NOFO often answers a whole family:
  the ARC non-construction guide covers 7 programs, the State Dept PRM refugee
  NOFO covers 6. Always check for a shared guide first.
- **Read the document, never a search summary.** Search results paraphrase
  section names, and a paraphrase is not good enough to tell an applicant what
  to write. When WebFetch returns binary PDF content it saves the file locally
  and prints the path; read that path with a page range.

Priority order:

1. **Programs already surfacing in shortlists.** If it reaches real users,
   its section list matters most. Start with the recurring federal disaster
   and forestry programs.
2. **Programs with a CFDA number.** These already have award precedent, so a
   section list makes the whole record useful. They also have findable NOFOs.
3. **Programs named in briefs we analyze.** `declared_targets` output is a
   direct signal of which funders people actually aim at.
4. **Everything else**, cheapest-to-verify first.

For each: find the agency's own required-sections list, add a
`grant_writing_templates.csv` row with `agency_verified=TRUE` and the source
URL, and record one example. Where the agency publishes nothing, record that
finding too so the next person does not re-search it.

Track findings in the program catalog and template CSVs with source URLs.
