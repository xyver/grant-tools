# Grant MCP Quickstart

Short onboarding for a new grant MCP user or client. This covers the tool
workflow once you are connected. For local setup, country data downloads, and
MCP client connection, see the [README quickstart](../README.md#quickstart).

## What This MCP Does

The Grants MCP is a phase-aware workflow tool for pre-submission grant work.

It helps with:

- prospect research
- fit and blocker analysis
- shortlist creation
- target-program selection
- memo support
- budget framework support
- submission-support checks

It does not handle:

- post-award compliance
- reimbursement/reporting
- closeout/audit work

## Endpoint

- `http://127.0.0.1:7020/mcp` after starting `server.py`

No bearer token is needed for the default local-only server.

## First-Time User Mental Model

Most users should think of the Grants MCP as a guided pre-submission workflow,
not as a one-shot grant search.

The default path is:

1. **Find credible targets:** "I have this project. What can I apply to?"
2. **Check action links:** open the official program/opportunity pages the MCP
   returns and confirm the current deadline, applicant rules, partner
   requirements, and internal university or organization deadline.
3. **Pick a target:** choose one program from the shortlist.
4. **Improve the application:** use historical winners, precedent summaries,
   budget sanity checks, and draft-structure review to make the proposal
   stronger.

The common first two calls are still:

1. describe the project
2. analyze the current phase

Then:

3. select a target program
4. ask for memo, budget, or submission-support help

Users may arrive out of order. If they start with "I already have a proposal,"
the MCP should still intake the project, identify or ask for the target
program, and spiral back to target-fit checks before doing proposal
improvement work.

## The First Two Calls

### Call 1: intake the project

Call:

- `grant_intake_or_update_project`

Required input:

- `project_input`

Optional useful structured fields:

- `country_code`
- `project_label`
- `profile_overrides.org_type`
- `profile_overrides.recipient_location_text`
- `profile_overrides.recipient_loc_id`
- `profile_overrides.project_area_text`
- `profile_overrides.project_area_loc_ids`
- `profile_overrides.place_of_performance_loc_id`
- `profile_overrides.has_government_partner`
- `profile_overrides.has_match_funding`
- `profile_overrides.target_award_amount`
- `profile_overrides.target_project_length_days`

### Place of performance is asked, never guessed

The MCP does not infer the project location from the narrative. A university,
partner, or funder named in a project description is a collaborator, not a
place of performance, and treating it as one silently relocates the project and
skews every eligibility check. If no location field is supplied, intake returns
`clarification_needed` with a `place_of_performance` question instead of
guessing.

Supply the location one of two ways:

- `project_area_text` - ordinary place names, semicolon-separated for several
  places (`"Buncombe County, North Carolina; Grayson County, Virginia"`).
  Commas are not separators, so `"Buncombe County, North Carolina"` stays one
  place.
- `project_area_loc_ids` - canonical loc_ids when you already know them.

`recipient_location_text` / `recipient_loc_id` are separate and describe where
the *applicant* is based, which is often not where the work happens.

### Several places of performance

Place of performance is multi-valued. Eligibility is geography-dependent, so a
coalition working across several counties or states faces a different eligible
program set in each one. Phase 1 scores the shortlist once per location and
merges the results:

- each entry in `top_matches` carries `eligible_locations`, the locations that
  produced it, so you can see what is portable and what is local
- `location_breakdown` reports the per-location match count and program ids
- `localization_summary.project_area_loc_ids` lists every location evaluated

A program listed for one location is not automatically available in the others.

### org_type is snapped to the eligibility vocabulary

`match_grant_programs` gates on an exact match against the CSV's
`eligible_applicant_types`, so a near-miss silently matches zero programs.
Common synonyms are normalized for you (`nonprofit`, `501c3`, `non-profit` all
become `nonprofit_501c3`; `university` becomes `higher_ed`). If a value cannot
be mapped, intake still proceeds but returns a `notes` entry naming the
supported values rather than returning an empty shortlist with no explanation.

Example:

```json
{
  "name": "grant_intake_or_update_project",
  "arguments": {
    "project_input": "We are a nonprofit 501c3 working on wildfire debris removal, wood products reuse, and disaster recovery in Appalachia.",
    "profile_overrides": {
      "recipient_location_text": "Buncombe County, North Carolina",
      "project_area_text": "Buncombe County, North Carolina; Yancey County, North Carolina; Grayson County, Virginia",
      "has_government_partner": true
    }
  }
}
```

Normal users do not need to know loc_ids. The MCP resolves ordinary place names
through the shared DaedalMap location spine when possible, then stores canonical
`recipient_loc_id`, `place_of_performance_loc_id`, and `project_area_loc_ids`
internally. If loc_ids are already known, passing them directly is still
supported.

Repeated calls with the same `session_id` accumulate the project narrative
rather than replacing it, so a short follow-up correction does not discard the
original brief.

Expected result:

- a `session_id`
- inferred phase
- normalized project profile
- clarification questions when key facts are missing

### Call 2: analyze the current phase

Call:

- `grant_analyze_current_phase`

Required input:

- `session_id`

Example:

```json
{
  "name": "grant_analyze_current_phase",
  "arguments": {
    "session_id": "gs_example"
  }
}
```

Expected result:

- top matches
- blockers
- precedent summary
- current opportunity summary where supported
- official program/opportunity links to check next
- recommended next step

Important distinction:

- US results can include a Grants.gov-backed `open_now_summary` where the
  backend supports open/closed opportunity checks.
- Canada results currently include official program-page checks in
  `open_now_summary` with `opportunity_status =
  program_page_check_required`. These are actionable links, but they are not a
  verified open/closed status.

## Typical Follow-Up Flow

After phase 1 returns a shortlist:

1. call `grant_select_target_program`
2. call `grant_analyze_current_phase` again with an `analysis_request`

Good phase 2 requests:

- `Compare my application framing to historical winners for this target.`
- `What precedent evidence should I cite before writing?`
- `Build memo talking points for this target.`
- `Help me build the budget framework.`
- `Review this proposal draft structure.`

Good phase 3 requests:

- `Is this program open right now?`
- `What kind of funding mechanism is this?`

## What To Tell Users Up Front

Teach these points first:

- the best first prompt is usually: "I have this project. What can I apply to?"
- start with `grant_intake_or_update_project`
- then use `grant_analyze_current_phase`
- check the returned official links before investing heavily in a target
- select a target before asking for application-improvement work
- the MCP asks clarification questions instead of guessing
- `recipient_loc_id` and `place_of_performance_loc_id` both matter
- this tool does not claim win probability
- this tool focuses on pre-submission work only

## Edge Cases

### User already has a draft

Do not skip discovery entirely. Intake the draft as project context, ask for or
infer the target program, then run Phase 2. If the target is missing, return to
Phase 1 long enough to identify likely programs.

### User already knows the target program

Use `grant_select_target_program` after intake, or ask the user to confirm the
target if it is not in the current shortlist. Then proceed to memo, budget,
draft-structure, or precedent support.

### User asks "who should I send this to?"

Treat this as Phase 1 unless a target is already selected. The MCP should
return plausible funders/programs and links, not invent named contacts unless a
source provides them.

### User asks for open grants only

Return open-opportunity checks where supported. Where the country lacks a
true open-opportunity backend, return official program-page checks and label
the status as unverified rather than pretending an open/closed check happened.

### User asks for odds of winning

Refuse the probability claim. The MCP can discuss fit, blockers, precedent,
award sizes, and application-strengthening actions, but not odds.

### User asks for post-award work

Return out of scope. The current MCP is pre-submission only.

## Best Teaching Surfaces

Teach the same message in three places:

1. MCP `initialize.instructions`
2. `prompts/list` starter prompts
3. a short human-readable quickstart doc like this one

Do not rely on the raw endpoint URL alone. Some clients surface tools and
instructions clearly. Others do not.
