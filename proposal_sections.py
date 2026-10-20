"""Proposal section model: a universal superset, and who requires what.

Two related problems this solves:

1. A grant brief arrives as prose. Reviewing or editing it means finding "the
   budget part" or "the partners part" by reading. Extracting named sections
   makes each piece addressable.

2. Feedback like "this proposal is weak" is useless. Feedback like "no
   measurable outcomes stated, and FEMA BRIC requires them" is actionable.
   That needs a per-funder view of which sections actually matter.

The section vocabulary here is the UNIVERSAL set - every section a proposal
could need. No single funder wants all of them. Requirements are resolved per
program from what the catalog already knows (funder_type, match/intermediary
flags), not from a hand-maintained per-program list, so this stays honest as
the catalog grows.

Extraction is deliberately heuristic: heading matches plus keyword evidence
over a window of text. It reports `present`, `thin`, or `missing` and shows the
evidence it used. It is a reviewer's checklist, not a parser, and it does not
claim to understand the proposal.
"""
from __future__ import annotations

import re
from typing import Any

# Universal section vocabulary. `heading` patterns match section titles in a
# structured document; `keywords` catch the same content in unstructured prose.
# `weight` marks how load-bearing a section is when it is required and absent.
PROPOSAL_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "title",
        "label": "Project title",
        "why": "Identifies the project on every form and reviewer sheet.",
        # Documents rarely label their own title, so the first substantive line
        # is treated as a title candidate - see _title_candidate().
        "heading": r"^\s*(project\s+)?titles?\b",
        "keywords": (),
        "weight": "low",
    },
    {
        "id": "applicant_identity",
        "label": "Applicant organization and legal status",
        "why": "The entity of record. Determines eligibility more than anything else in the proposal.",
        "heading": r"\b(about|applicant|organization|who we are|prepared by)\b",
        "keywords": ("501c3", "501(c)(3)", "nonprofit", "incorporated", "llc", "tribal government",
                     "municipality", "university", "foundation", "our organization"),
        "weight": "high",
    },
    {
        "id": "contact",
        "label": "Contact person",
        "why": "Program officers need a named human with a role, phone, and email.",
        "heading": r"\bcontact\b",
        "keywords": ("@", "phone", "email", "president", "director", "principal investigator"),
        "weight": "low",
    },
    {
        "id": "summary",
        "label": "Project summary or abstract",
        "why": "Often the only section every reviewer reads.",
        "heading": r"\b(summary|abstract|overview|executive summary|objectives?)\b",
        "keywords": ("this project", "the project will", "we propose", "objective"),
        "weight": "high",
    },
    {
        "id": "need_statement",
        "label": "Statement of need or problem",
        "why": "Establishes why the work is necessary, with evidence rather than assertion.",
        "heading": r"\b(need|problem|background|context|challenge|justification)\b",
        "keywords": ("because", "as a result", "damage", "risk", "gap", "shortage", "impacted",
                     "affected", "crisis", "unmet"),
        "weight": "high",
    },
    {
        "id": "location",
        "label": "Place of performance",
        "why": "Drives geographic eligibility. Often differs from where the applicant is based.",
        "heading": r"\b(locations?|geograph\w*|service areas?|project areas?|forest areas?|sites?|where)\b",
        "keywords": ("county", "counties", "state of", "region", "district", "parish", "territory"),
        "weight": "high",
    },
    {
        "id": "activities",
        "label": "Activities and work plan",
        "why": "What will actually be done, in what order, by whom.",
        # "Collaboration", "Initiatives", and "Program" are common real-world
        # headings for the work plan; without them the activities section reads
        # as missing on documents that plainly describe the work.
        "heading": (
            r"\b(activities|approach|work ?plan|scope of work|methodolog\w*|implementation|"
            r"tasks?|collaborations?|operations?|phases?)\b"
        ),
        "keywords": ("we will", "phase", "step", "process", "deliver", "conduct", "perform",
                     "working with", "identify", "coordinate", "provide", "contract with"),
        "weight": "high",
    },
    {
        "id": "partners",
        "label": "Partners and their roles",
        "why": "Some programs require a government or nonprofit co-applicant; unnamed partners read as aspirational.",
        "heading": r"\b(partners?|partnerships?|coalitions?|stakeholders?|leadership|teams?|consortium)\b",
        "keywords": ("in partnership with", "collaboration", "partners include", "mou",
                     "letter of support", "co-applicant", "subrecipient"),
        "weight": "high",
    },
    {
        "id": "outcomes",
        "label": "Measurable outcomes",
        "why": "Reviewers score on measurable results, not described effort.",
        "heading": r"\b(outcomes?|results?|impacts?|goals?|deliverables?|metrics|performance measures?)\b",
        "keywords": ("we expect", "will result in", "measure", "indicator", "target of",
                     "beneficiaries", "acres", "households served", "jobs created"),
        "weight": "high",
    },
    {
        "id": "evaluation",
        "label": "Evaluation plan",
        "why": "How success gets verified, and by whom.",
        "heading": r"\b(evaluation|monitoring|assessment|reporting)\b",
        "keywords": ("we will track", "evaluate", "baseline", "data collection", "report quarterly"),
        "weight": "medium",
    },
    {
        "id": "timeline",
        "label": "Timeline or period of performance",
        "why": "Must fit the program's allowed project length.",
        "heading": r"\b(timelines?|schedules?|period of performance|duration|milestones?|outlook)\b",
        "keywords": ("month", "months", "year outlook", "quarter", "by 20", "start date",
                     "multi-year", "phase 1"),
        "weight": "high",
    },
    {
        "id": "budget",
        "label": "Budget and cost basis",
        "why": "Unit costs and assumptions, not just a total.",
        "heading": r"\b(budget|cost|financial|expenses|pricing)\b",
        "keywords": ("$", "per day", "per unit", "rate", "personnel", "equipment", "indirect",
                     "line item"),
        "weight": "high",
    },
    {
        "id": "funding_request",
        "label": "Amount requested",
        "why": "A specific ask, checked against the program's typical award size.",
        "heading": r"\b(funding requests?|amount requested|asks?|requests?)\b",
        "keywords": ("requesting", "we seek", "total request", "award of"),
        "weight": "high",
    },
    {
        "id": "match_funding",
        "label": "Match or cost share",
        "why": "Required by many programs; an unaddressed match requirement is a hard blocker.",
        "heading": r"\b(match|cost share|leverage|in-kind)\b",
        "keywords": ("cost share", "matching funds", "in-kind", "leveraged", "non-federal share"),
        "weight": "high",
    },
    {
        "id": "capacity",
        "label": "Organizational capacity and past performance",
        "why": "Evidence the applicant can execute an award of this size.",
        "heading": r"\b(capacity|qualifications|experience|past performance|track record|history)\b",
        "keywords": ("we have previously", "prior award", "years of experience", "our staff",
                     "successfully completed", "managed"),
        "weight": "medium",
    },
    {
        "id": "sustainability",
        "label": "Sustainability after the award",
        "why": "What happens when the grant period ends.",
        "heading": r"\b(sustainab|long.term|after the grant|continuation|maintenance)\b",
        "keywords": ("beyond the grant", "ongoing", "self-sustaining", "revenue", "long term outlook"),
        "weight": "medium",
    },
    {
        "id": "compliance_registration",
        "label": "Registrations and compliance",
        "why": "SAM.gov/UEI registration gates federal submission and takes weeks to obtain.",
        "heading": r"\b(compliance|registration|certification|assurances)\b",
        "keywords": ("sam.gov", "sam registration", "uei", "unique entity id", "duns", "ein",
                     "single audit", "indirect cost rate"),
        "weight": "high",
    },
    {
        "id": "target_funder",
        "label": "Intended funder or program",
        "why": "States which door this proposal is aimed at, so fit can be checked.",
        "heading": r"\b(funding sources|prospective funders|grant opportunities|target program)\b",
        "keywords": ("we plan to apply", "nofo", "solicitation", "funding opportunity"),
        "weight": "medium",
    },
)

SECTIONS_BY_ID = {section["id"]: section for section in PROPOSAL_SECTIONS}

# Sections every proposal needs regardless of funder.
CORE_SECTIONS = (
    "applicant_identity", "summary", "need_statement", "location",
    "activities", "outcomes", "budget", "funding_request",
)

# Additional expectations by funder_type, from the catalog's own column.
FUNDER_TYPE_SECTIONS = {
    "federal": ("compliance_registration", "timeline", "evaluation", "capacity", "partners"),
    "state": ("timeline", "evaluation", "capacity"),
    "private_foundation": ("sustainability", "capacity", "contact"),
    "defense_international": ("compliance_registration", "timeline", "evaluation", "capacity"),
}

STATUS_PRESENT = "present"
STATUS_THIN = "thin"
STATUS_MISSING = "missing"

# A section backed only by scattered keywords, with little text, is a mention
# rather than a section. This is the line between "present" and "thin".
THIN_CHAR_THRESHOLD = 200


def _normalize(text: Any) -> str:
    return re.sub(r"[ \t]+", " ", str(text or "")).strip()


def _segment_by_headings(text: str) -> list[tuple[str, str]]:
    """Split into (heading, body) pairs on markdown or short title-case lines."""
    lines = text.split("\n")
    segments: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        stripped = line.strip()
        is_md = bool(re.match(r"^#{1,6}\s+\S", stripped))
        # A short line with no terminal punctuation reads as a heading.
        is_bare = bool(
            stripped
            and len(stripped) <= 60
            and not stripped.endswith((".", ",", ";", ":"))
            and re.match(r"^[A-Z0-9]", stripped)
            and len(stripped.split()) <= 7
        )
        if is_md or is_bare:
            segments.append((re.sub(r"^#{1,6}\s*", "", stripped), []))
        else:
            segments[-1][1].append(line)
    return [(head, "\n".join(body)) for head, body in segments]


def _title_candidate(text: str) -> str | None:
    """First substantive line of a document, which is almost always the title.

    Proposals rarely include a line that says "Title:", so requiring that label
    reported a missing title on documents whose first line was the title.
    """
    for line in text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if len(stripped) < 4:
            continue
        if stripped.lower().startswith(("prepared for", "prepared by", "date", "contact")):
            continue
        return stripped[:120] if len(stripped) <= 160 else None
    return None


def extract_sections(text: Any) -> dict[str, dict[str, Any]]:
    """Map proposal prose onto the universal section vocabulary.

    Returns one entry per section id with status, the evidence used, and the
    amount of supporting text found. Heuristic by design - it reports what it
    matched so a human can disagree with it.
    """
    body = _normalize(text)
    lowered = body.lower()
    segments = _segment_by_headings(body)

    results: dict[str, dict[str, Any]] = {}
    for section in PROPOSAL_SECTIONS:
        sid = section["id"]
        evidence: list[str] = []
        chars = 0

        for heading, seg_body in segments:
            if heading and re.search(section["heading"], heading.lower()):
                evidence.append(f"heading: {heading[:60]}")
                chars += len(seg_body.strip())

        hits = [k for k in section["keywords"] if k in lowered]
        if hits:
            evidence.append("mentions: " + ", ".join(hits[:4]))
            if not chars:
                # No matching heading, so approximate the supporting text as the
                # sentences containing the hits.
                for hit in hits[:6]:
                    for m in re.finditer(re.escape(hit), lowered):
                        start = max(0, m.start() - 120)
                        chars += len(body[start:m.end() + 120])
                        break

        if sid == "title" and not evidence:
            candidate = _title_candidate(body)
            if candidate:
                evidence.append(f"first line: {candidate[:60]}")
                chars = THIN_CHAR_THRESHOLD  # a title is complete at one line

        if not evidence:
            status = STATUS_MISSING
        elif chars < THIN_CHAR_THRESHOLD:
            status = STATUS_THIN
        else:
            status = STATUS_PRESENT

        results[sid] = {
            "section_id": sid,
            "label": section["label"],
            "status": status,
            "evidence": evidence,
            "supporting_chars": chars,
        }
    return results


def required_sections_for_program(program: dict[str, Any]) -> list[str]:
    """Which sections this specific program actually needs.

    Derived from catalog columns rather than a hand-maintained per-program
    list: funder_type sets the baseline, and the match/intermediary flags add
    the sections those requirements imply.
    """
    required = list(CORE_SECTIONS)
    funder_type = str(program.get("funder_type") or "").strip().lower()
    for sid in FUNDER_TYPE_SECTIONS.get(funder_type, ()):
        if sid not in required:
            required.append(sid)

    if str(program.get("requires_match_funding") or "").strip().upper() == "TRUE":
        if "match_funding" not in required:
            required.append("match_funding")
    if str(program.get("requires_government_intermediary") or "").strip().upper() == "TRUE":
        if "partners" not in required:
            required.append("partners")
    return required


def _citations_from_template(template: dict[str, Any]) -> dict[str, Any]:
    """Every source backing a template, so requirements can be attributed.

    Advice with no citation is indistinguishable from advice we invented. The
    primary source_url is listed first; guidance_urls carries the rest (scoring
    rubric, forms page, application guide).
    """
    def _clean(value: Any) -> str | None:
        # Empty CSV cells arrive as the float nan via pandas; str() would turn
        # that into the literal "nan" and leak it into user-facing citations.
        text = str(value if value is not None else "").strip()
        return text if text and text.lower() != "nan" else None

    primary = _clean(template.get("source_url"))
    raw = template.get("guidance_urls")
    if isinstance(raw, (list, tuple)):
        extra = [str(u).strip() for u in raw if str(u).strip()]
    else:
        extra = [u.strip() for u in str(raw or "").split("|") if u.strip()]
    urls: list[str] = []
    for url in ([primary] if primary else []) + extra:
        if url and url not in urls:
            urls.append(url)
    return {
        "primary": primary,
        "urls": urls,
        "checked_date": _clean(template.get("checked_date")),
    }


def resolve_requirements(
    program: dict[str, Any], template: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Decide which section list governs this program, and say where it came from.

    Only 10 of 127 catalog programs have a writing template, and only some of
    those are agency-verified. Where a verified template exists it is the
    authority and this module must not override it. Everywhere else the
    universal set plus funder_type defaults is the floor. The provenance is
    returned so a caller never presents an inferred list as if the agency
    published it.
    """
    if template and template.get("narrative_sections"):
        raw_verified = template.get("agency_verified")
        verified = raw_verified is True or str(raw_verified or "").strip().upper() == "TRUE"
        # get_grant_writing_template() returns a parsed list; the raw CSV cell is
        # a ';'-delimited string. Accept either rather than stringifying a list.
        raw_sections = template["narrative_sections"]
        if isinstance(raw_sections, (list, tuple)):
            sections = [str(s).strip() for s in raw_sections if str(s).strip()]
        else:
            sections = [s.strip() for s in str(raw_sections).split(";") if s.strip()]
        citations = _citations_from_template(template)
        return {
            "source": "agency_template_verified" if verified else "agency_template_inferred",
            "template_id": template.get("template_id"),
            "agency_verified": verified,
            "agency_section_names": sections,
            "universal_sections": required_sections_for_program(program),
            "guidance_urls": citations["urls"],
            "checked_date": citations["checked_date"],
            "attribution": (
                f"Based on {program.get('agency_or_funder') or 'the funder'}'s published "
                f"guidance ({citations['primary'] or 'source URL not recorded'}), a complete "
                "submission needs the sections listed here."
                if verified else
                f"Section list inferred for this funder, not published for this specific "
                f"program (nearest guidance: {citations['primary'] or 'none recorded'}). "
                "Verify against the current NOFO before relying on it."
            ),
            "note": (
                "Agency-published section list governs. The universal set is listed "
                "alongside it as a completeness floor, not as a requirement."
                if verified else
                "Section list inferred for this agency, not published. Verify against "
                "the current NOFO before relying on it."
            ),
        }
    return {
        "source": "universal_plus_funder_type_default",
        "template_id": None,
        "agency_verified": False,
        "agency_section_names": [],
        "universal_sections": required_sections_for_program(program),
        "guidance_urls": [u for u in [program.get("source_url")] if u],
        "checked_date": None,
        "attribution": (
            "No published section list has been recorded for this program, so nothing "
            "here can be attributed to the funder. The sections below are a generic "
            "completeness floor."
        ),
        "note": (
            "No writing template exists for this program. Requirements below are the "
            "universal proposal set plus defaults for this funder type - a sanity "
            "floor, not this agency's published list. Check the NOFO."
        ),
    }


def assess_against_program(
    extracted: dict[str, dict[str, Any]], program: dict[str, Any]
) -> dict[str, Any]:
    """Gap report for one target program."""
    required = required_sections_for_program(program)
    missing, thin = [], []
    for sid in required:
        status = (extracted.get(sid) or {}).get("status", STATUS_MISSING)
        if status == STATUS_MISSING:
            missing.append(sid)
        elif status == STATUS_THIN:
            thin.append(sid)

    actions: list[str] = []
    for sid in missing:
        section = SECTIONS_BY_ID.get(sid, {})
        actions.append(f"Add {section.get('label', sid)}. {section.get('why', '')}".strip())
    for sid in thin:
        section = SECTIONS_BY_ID.get(sid, {})
        actions.append(f"Expand {section.get('label', sid)} - currently only a passing mention.")

    return {
        "program_id": program.get("program_id"),
        "program_name": program.get("program_name"),
        "required_sections": required,
        "missing_sections": missing,
        "thin_sections": thin,
        "satisfied_count": len(required) - len(missing) - len(thin),
        "required_count": len(required),
        "recommended_actions": actions,
    }


def summarize(extracted: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Funder-independent completeness across the universal vocabulary."""
    counts = {STATUS_PRESENT: [], STATUS_THIN: [], STATUS_MISSING: []}
    for sid, entry in extracted.items():
        counts[entry["status"]].append(sid)
    core_missing = [s for s in CORE_SECTIONS if s in counts[STATUS_MISSING]]
    return {
        "present": sorted(counts[STATUS_PRESENT]),
        "thin": sorted(counts[STATUS_THIN]),
        "missing": sorted(counts[STATUS_MISSING]),
        "core_sections_missing": core_missing,
        "note": (
            "Section detection is heuristic - it matches headings and keywords and "
            "reports the evidence it used. Treat it as a reviewer checklist, not as "
            "an understanding of the proposal."
        ),
    }
