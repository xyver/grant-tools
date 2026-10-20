# Grant MCP Contract v1

First concrete contract draft for the phase-aware grant MCP.

This document defines the first two core tools:

- `grant_intake_or_update_project`
- `grant_analyze_current_phase`

These are the minimum contract surfaces needed to make the remote grant MCP
behave more like the local tool flow:

- capture the project state explicitly
- infer or confirm the current pre-submission phase
- ask for missing critical information cleanly
- run the correct phase-specific analysis without forcing the client to
  orchestrate the whole grant workflow itself

This is a contract doc, not an implementation doc. It focuses on:

- tool purposes
- inputs
- outputs
- required behavior
- error and clarification shapes
- how each tool maps onto the existing backend

## Current Hosted Contract Note

The hosted Grants MCP at `/mcp-private/grants` currently exposes a session-based
two-step flow:

1. `grant_intake_or_update_project`
2. `grant_analyze_current_phase`

The intake tool accepts:

- `project_input`
- optional `session_id`
- optional `project_label`
- optional `country_code`
- optional `phase_hint`
- optional `profile_overrides`
- optional `allow_inference`

The analysis tool accepts:

- `session_id`
- optional `analysis_request`
- optional `phase_override`
- optional `depth`
- optional `refresh_profile_from_text`

Legacy intake-style argument names are not part of the live contract and should
not be sent by browser clients:

- `organization_name`
- `project_title`
- `project_summary`
- `phase`
- `geography`

If a client has older prompt scaffolding that still emits those fields, update
the scaffold rather than expecting the server to normalize them silently.

Related docs:

- [grant_phase_aware_mcp_design.md](grant_phase_aware_mcp_design.md)
- [grant_mcp_mode.md](grant_mcp_mode.md)
- [../README.md](../README.md)

## Scope

This contract is only for the pre-submission phases:

1. Prospect research
2. Proposal development
3. Submission and review support

Out of scope:

4. Award and compliance setup
5. Post-award implementation and reporting
6. Closeout and stewardship

## Contract Principles

1. The MCP surface must be phase-aware.
2. The MCP surface must preserve structured working state.
3. The MCP surface must prefer clarification over silent guessing for critical
   missing fields.
4. The MCP surface must return shaped results, not raw internal helper output
   dumps.
5. The MCP surface must preserve country capability honesty.
6. The MCP surface must not claim win probability.

## Shared Enums

### `phase_id`

Allowed values:

- `phase_1_prospect_research`
- `phase_2_proposal_development`
- `phase_3_submission_support`
- `out_of_scope_post_award`
- `ambiguous`

### `field_status`

Allowed values:

- `confirmed`
- `inferred`
- `missing`
- `conflicting`

### `tool_outcome`

Allowed values:

- `ok`
- `clarification_needed`
- `out_of_scope`
- `unsupported_country_capability`
- `unsupported_claim_type`
- `error`

## Shared Session Model

Both tools operate against a server-side session object.

Minimum session fields:

- `session_id`
- `project_label`
- `current_phase`
- `phase_confidence`
- `country_code`
- `project_profile`
- `project_profile_missing_fields`
- `current_target_programs`
- `current_selected_program_id`
- `current_scored_results`
- `current_memo_context`
- `current_budget_context`
- `current_draft_context`
- `open_questions`
- `last_tool_outputs`

Client rule:

- the client may provide `session_id`
- if omitted on first use, the server creates one and returns it

## Shared Project Profile Shape

Normalized project profile fields:

- `project_description`
- `org_name`
- `org_type`
- `sector_tags`
- `recipient_loc_id`
- `place_of_performance_loc_id`
- `target_country_code`
- `has_government_partner`
- `has_match_funding`
- `target_award_amount`
- `target_project_length_days`

Each field should be represented as:

- `value`
- `status`
- `source`

Example:

```json
{
  "org_type": {
    "value": "nonprofit_501c3",
    "status": "confirmed",
    "source": "user_input"
  }
}
```

## Tool 1: `grant_intake_or_update_project`

### Purpose

Create or update the normalized project/session state.

This is the first tool the client should call when:

- the user describes a project for the first time
- the user provides new information that changes the project profile
- the user wants the system to infer or refresh the current phase

### Inputs

Required:

- `project_input`

Optional:

- `session_id`
- `project_label`
- `country_code`
- `phase_hint`
- `profile_overrides`
- `allow_inference`

### Input Shape

#### `project_input`

Free text or semi-structured text from the user describing the project and
their request.

Recommended browser-LLM pattern:

- put the user's full project description in `project_input`
- use `project_label` only as a short session label
- use `profile_overrides` for structured facts already known with confidence

#### `phase_hint`

Optional explicit hint from the client:

- `phase_1_prospect_research`
- `phase_2_proposal_development`
- `phase_3_submission_support`

#### `profile_overrides`

Optional structured overrides for fields already known or gathered elsewhere.

Example:

```json
{
  "org_type": "higher_ed",
  "recipient_loc_id": "USA-VA",
  "target_country_code": "VEN"
}
```

#### `allow_inference`

Boolean.

Meaning:

- `true` = the server may infer non-critical fields from text
- `false` = only explicit user-provided fields should be set as values; other
  uncertain fields remain missing

Recommended default:

- `true`

### Required Behavior

The tool must:

1. Create or load the session.
2. Merge new input into the session profile.
3. Normalize fields into the shared project profile shape.
4. Infer likely `current_phase` unless a clear `phase_hint` overrides it.
5. Compute `project_profile_missing_fields`.
6. Detect critical conflicts.
7. Return a structured summary of the current state.

The tool must not:

- run full grant analysis
- silently choose a target program
- hide conflicts between old and new profile data

### Critical Missing Field Rules

These should trigger clarification when relevant:

Phase 1 likely critical fields:

- `org_type`
- one of `recipient_loc_id` or `target_country_code`
- `place_of_performance_loc_id` when domestic project geography is central

Phase 2 likely critical fields:

- `current_selected_program_id` or a clear shortlist
- `target_award_amount` for budget framing
- `target_project_length_days` when duration-sensitive framing is requested

Phase 3 likely critical fields:

- specific target program
- whether the user is asking about general program availability or a concrete
  current opportunity

### Output Shape

```json
{
  "tool_name": "grant_intake_or_update_project",
  "outcome": "ok",
  "session_id": "gs_123",
  "project_label": "Woodlands Mission",
  "current_phase": "phase_1_prospect_research",
  "phase_confidence": 0.92,
  "country_code": "US",
  "project_profile": {},
  "project_profile_missing_fields": [],
  "conflicting_fields": [],
  "open_questions": [],
  "next_recommended_tool": "grant_analyze_current_phase",
  "notes": []
}
```

### Clarification Output Shape

```json
{
  "tool_name": "grant_intake_or_update_project",
  "outcome": "clarification_needed",
  "session_id": "gs_123",
  "current_phase": "phase_1_prospect_research",
  "project_profile": {},
  "project_profile_missing_fields": [
    "recipient_loc_id",
    "place_of_performance_loc_id"
  ],
  "questions": [
    {
      "question_id": "recipient_vs_performance",
      "prompt": "Is the applying organization in the same place the funded work will happen?",
      "fields_unblocked": [
        "recipient_loc_id",
        "place_of_performance_loc_id"
      ]
    }
  ],
  "resume_hint": "grant_intake_or_update_project"
}
```

### Error Cases

- `unsupported_country_capability`
  - if `country_code` is provided but there is no backend
- `out_of_scope`
  - if the request clearly concerns post-award work
- `error`
  - malformed input, internal exception, or invalid override schema

## Tool 2: `grant_analyze_current_phase`

### Purpose

Run the correct phase-specific workflow against the stored session state.

This is the main composite analysis tool and should be the default "help me from
where I am now" surface.

### Inputs

Required:

- `session_id`

Optional:

- `analysis_request`
- `phase_override`
- `depth`
- `refresh_profile_from_text`

### Input Shape

#### `analysis_request`

Optional free text describing the immediate ask for this step, such as:

- "What grants fit this project?"
- "Turn this into memo talking points"
- "Is this program open right now?"

#### `phase_override`

Optional explicit override when the client/user wants control.

Allowed values:

- `phase_1_prospect_research`
- `phase_2_proposal_development`
- `phase_3_submission_support`

#### `depth`

Allowed values:

- `light`
- `standard`
- `full`

Recommended default:

- `standard`

Meaning:

- `light`
  - minimal workflow for the current phase
- `standard`
  - normal recommended workflow
- `full`
  - include more expensive or broader sub-steps where supported

#### `refresh_profile_from_text`

Boolean.

Meaning:

- whether to merge `analysis_request` back into the session profile before
  routing

Recommended default:

- `true`

### Required Behavior

The tool must:

1. Load the session.
2. Optionally merge new request text into the session profile.
3. Determine the active phase from:
   - explicit `phase_override`
   - stored `current_phase`
   - new request text if it clearly changes the phase
4. Refuse or clarify if critical information is missing.
5. Route to the phase-specific internal workflow.
6. Return a phase-specific shaped result.

The tool must not:

- collapse all three phases into one giant combined response
- silently run post-award workflows
- hide unsupported country capabilities

## Phase 1 Routing Contract

When active phase is `phase_1_prospect_research`, the tool should normally:

1. run matching
2. run readiness scoring when supported and enough inputs exist
3. add precedent summary for top matches
4. add current opportunity summary when relevant
5. return shortlist + blockers + next step

Current backend mapping:

- `match_grant_programs`
- `score_and_rank_programs`
- `get_grant_precedents`
- `get_country_precedent`
- `check_open_opportunity`
- `find_open_opportunities_live`

### Phase 1 Output Shape

```json
{
  "tool_name": "grant_analyze_current_phase",
  "outcome": "ok",
  "session_id": "gs_123",
  "phase_run": "phase_1_prospect_research",
  "phase_changed": false,
  "selected_workflow_steps": [
    "match_grant_programs",
    "score_and_rank_programs",
    "get_grant_precedents"
  ],
  "top_matches": [],
  "blocked_but_workable_matches": [],
  "key_blockers": [],
  "precedent_summary": [],
  "open_now_summary": [],
  "recommended_next_step": {
    "type": "move_to_phase_2_or_select_target",
    "message": "Select one target program before memo and budget work."
  },
  "limitations": []
}
```

## Phase 2 Routing Contract

When active phase is `phase_2_proposal_development`, the tool should choose the
correct sub-workflow based on available state.

Normal Phase 2 branches:

- internal buy-in / memo support
- budget framework
- draft-structure review
- precedent-text reference support

Current backend mapping:

- `build_memo_talking_points`
- `build_budget_framework`
- `score_proposal_structure`
- `find_similar_precedent_text`

If no target program is selected and the Phase 2 request depends on one:

- return `clarification_needed`

### Phase 2 Output Shape

```json
{
  "tool_name": "grant_analyze_current_phase",
  "outcome": "ok",
  "session_id": "gs_123",
  "phase_run": "phase_2_proposal_development",
  "phase_changed": false,
  "selected_program_id": "usfs_wood_innovations",
  "selected_workflow_steps": [
    "build_memo_talking_points",
    "build_budget_framework"
  ],
  "memo_support": {},
  "budget_framework": {},
  "draft_structure_review": null,
  "precedent_text_support": null,
  "recommended_next_step": {
    "type": "confirm_submission_target",
    "message": "If this is the chosen target, the next step is submission-support confirmation."
  },
  "limitations": []
}
```

## Phase 3 Routing Contract

When active phase is `phase_3_submission_support`, the tool should focus on
pre-submission support only.

Normal Phase 3 branches:

- current opportunity check
- funding mechanism explanation
- submission-prep reminder checklist

Current backend mapping:

- `check_open_opportunity`
- `find_open_opportunities_live`

### Phase 3 Output Shape

```json
{
  "tool_name": "grant_analyze_current_phase",
  "outcome": "ok",
  "session_id": "gs_123",
  "phase_run": "phase_3_submission_support",
  "phase_changed": false,
  "selected_program_id": "usaid_omnibus",
  "selected_workflow_steps": [
    "check_open_opportunity"
  ],
  "opportunity_status": {},
  "funding_mechanism_summary": {},
  "submission_support_notes": [],
  "review_process_limitations": [
    "The tool cannot observe internal agency panel or merit review outcomes."
  ],
  "recommended_next_step": {
    "type": "submission_confirmation",
    "message": "Confirm the exact funding notice, deadline posture, and final application requirements outside the tool."
  }
}
```

## Clarification Contract For `grant_analyze_current_phase`

If the phase is understood but critical information is missing, return:

```json
{
  "tool_name": "grant_analyze_current_phase",
  "outcome": "clarification_needed",
  "session_id": "gs_123",
  "phase_run": "phase_2_proposal_development",
  "questions": [
    {
      "question_id": "select_target_program",
      "prompt": "Which program from the current shortlist are you actively developing the proposal for?",
      "fields_unblocked": [
        "current_selected_program_id"
      ]
    }
  ],
  "resume_hint": "grant_analyze_current_phase"
}
```

## Capability Gating Rules

The tool layer must respect backend capability truth.

Current expected behavior:

- `US`
  - Phase 1, 2, and partial 3 supported
- `CAN`
  - discovery and precedent only
  - no readiness scoring
  - no live opportunity check
  - no writing-template or proposal-structure pathway yet

Contract rule:

- unsupported capabilities must return `unsupported_country_capability`
- do not silently return empty results or downgrade to a fake success path

## Phase Transition Rules

The tool may update `current_phase` when the evidence is strong.

Examples:

- a project with shortlisted targets and a request for memo talking points
  should shift from Phase 1 to Phase 2
- a project with a selected program and a request to confirm whether it is open
  should shift from Phase 2 to Phase 3

When phase changes:

- return `phase_changed: true`
- return `prior_phase`
- return `new_phase`

Example:

```json
{
  "phase_changed": true,
  "prior_phase": "phase_1_prospect_research",
  "new_phase": "phase_2_proposal_development"
}
```

## Mapping To Existing Backend Code

Initial implementation should keep the protocol layer thin and map onto existing
code.

### `grant_intake_or_update_project`

New MCP-layer responsibilities:

- session creation and update
- profile normalization
- phase inference
- clarification generation

No direct one-function backend equivalent exists yet.

### `grant_analyze_current_phase`

Phase 1:

- `grant_analyzer.get_backend(country_code)`
- `match_grant_programs`
- `score_and_rank_programs`
- `get_grant_precedents`
- `get_country_precedent`
- `check_open_opportunity`
- `find_open_opportunities_live`

Phase 2:

- `build_memo_talking_points`
- `build_budget_framework`
- `score_proposal_structure`
- `find_similar_precedent_text`

Phase 3:

- `check_open_opportunity`
- `find_open_opportunities_live`

## QA Cases For This Contract

Minimum contract QA:

1. New Phase 1 project description creates a session and returns a normalized
   project profile.
2. Missing geography fields trigger clarification instead of silent scoring.
3. Woodlands Mission Phase 1 run returns shortlist + blockers.
4. Transition from shortlist to memo request changes phase to Phase 2.
5. Phase 2 without selected target returns clarification.
6. Phase 2 with selected target returns memo or budget output.
7. Phase 3 request returns current opportunity status and mechanism notes.
8. Post-award request returns `out_of_scope`.
9. Canada Phase 2 request returns `unsupported_country_capability`.

## Open Follow-On Contracts

This doc intentionally does not fully specify:

- `grant_set_phase`
- `grant_select_target_program`
- explicit advanced tools for each phase
- prompt/resource publication
- OAuth/token auth envelopes

Those should be specified after these first two tools are stable.

## Decision

The first contract version should start with:

- one tool to build and update the structured project state
- one tool to run the correct phase-aware workflow from that state

That is the smallest contract that still preserves the main advantages of the
local grant workflow in a remote MCP setting.
