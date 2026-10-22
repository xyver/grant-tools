"""
Automated test suite for the grant_analyzer vertical - the real gap
flagged by an external architecture review (2026-06-29): every previous
validation in this module was a print-and-eyeball `__main__` block, not
an assertion a regression would actually fail. This suite covers:

  - tristate.parse_tristate - the bug this whole refactor started from
  - us_backend: regression-tests the real Woodlands Mission case (known
    blockers, known directly-eligible programs) and the international/
    USAID Venezuela case (known country-precedent numbers)
  - can_backend: regression-tests the real BC wildfire case and the
    DFAA/Community Resilience Fund precedent merges (known numbers,
    confirmed against the by-hand totals computed during the eligibility-
    table research)
  - schema.validate_programs_df - both real CSVs must pass, and a
    deliberately broken CSV must fail loudly
  - capability flags - can_backend's unsupported functions must raise,
    never silently return an empty/zero result

Not a snapshot/regression suite for every one of the 140 combined CSV
rows - that's real future work (see grant_analyzer/README.md's next
steps), not done here. This is the floor: the specific real bugs and
real test cases already validated by hand in this project's history
should never regress silently again.

Usage:
    From this directory:
        python -m unittest test_grant_analyzer -v
    From the repo root:
        python -m unittest county-map-private/live/private_mcp_tools/grant_analyzer/test_grant_analyzer.py -v
    Or:
        python county-map-private/live/private_mcp_tools/grant_analyzer/test_grant_analyzer.py
"""
import os
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_backend
import can_backend
import grant_analyzer
from tristate import parse_tristate
from schema import validate_programs_df, SchemaError
import proposal_sections
import runtime_paths


def requires_country_data(iso3):
    """Skip a test when that country's bundle is not under GRANT_DATA_ROOT."""
    present = (runtime_paths.DATA_ROOT / "countries" / iso3).is_dir()
    return unittest.skipUnless(present, f"{iso3} grant data bundle not installed under GRANT_DATA_ROOT")

_PRIOR_GRANT_DATA_MODE = None


def setUpModule():
    global _PRIOR_GRANT_DATA_MODE
    _PRIOR_GRANT_DATA_MODE = os.environ.get("GRANT_MCP_DATA_MODE")
    os.environ["GRANT_MCP_DATA_MODE"] = "local"


def tearDownModule():
    if _PRIOR_GRANT_DATA_MODE is None:
        os.environ.pop("GRANT_MCP_DATA_MODE", None)
    else:
        os.environ["GRANT_MCP_DATA_MODE"] = _PRIOR_GRANT_DATA_MODE


class TristateTests(unittest.TestCase):
    def test_true_variants(self):
        self.assertTrue(parse_tristate("TRUE"))
        self.assertTrue(parse_tristate("true"))
        self.assertTrue(parse_tristate(" True "))

    def test_false_variants(self):
        self.assertFalse(parse_tristate("FALSE"))
        self.assertFalse(parse_tristate("false"))

    def test_unknown_and_garbage_return_none_not_false(self):
        # The real bug this module exists to prevent: UNKNOWN (or
        # anything unrecognized) must never collapse to False.
        self.assertIsNone(parse_tristate("UNKNOWN"))
        self.assertIsNone(parse_tristate(""))
        self.assertIsNone(parse_tristate(None))
        self.assertIsNone(parse_tristate(float("nan")))
        self.assertIsNone(parse_tristate("Tru"))  # typo, not a valid TRUE


class SchemaValidationTests(unittest.TestCase):
    def test_real_us_csv_passes(self):
        df = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)
        validate_programs_df(df, "us")  # raises on failure

    def test_real_can_csv_passes(self):
        df = pd.read_csv(can_backend.PROGRAMS_CSV, dtype=str)
        validate_programs_df(df, "can")  # raises on failure

    def test_duplicate_program_id_fails_loudly(self):
        df = pd.DataFrame([
            {"program_id": "x", "program_name": "A", "funder_type": "federal",
             "agency_or_funder": "Agency", "funds_what": "direct_project",
             "eligible_applicant_types": "nonprofit", "requires_government_intermediary": "TRUE",
             "requires_match_funding": "FALSE", "geography_scope": "national",
             "sector_tags": "x", "typical_award_size": "small", "notes": "", "source_url": ""},
            {"program_id": "x", "program_name": "B", "funder_type": "federal",
             "agency_or_funder": "Agency", "funds_what": "direct_project",
             "eligible_applicant_types": "nonprofit", "requires_government_intermediary": "TRUE",
             "requires_match_funding": "FALSE", "geography_scope": "national",
             "sector_tags": "x", "typical_award_size": "small", "notes": "", "source_url": ""},
        ])
        with self.assertRaises(SchemaError):
            validate_programs_df(df, "test")

    def test_invalid_tristate_value_fails_loudly(self):
        df = pd.DataFrame([
            {"program_id": "x", "program_name": "A", "funder_type": "federal",
             "agency_or_funder": "Agency", "funds_what": "direct_project",
             "eligible_applicant_types": "nonprofit", "requires_government_intermediary": "MAYBE",
             "requires_match_funding": "FALSE", "geography_scope": "national",
             "sector_tags": "x", "typical_award_size": "small", "notes": "", "source_url": ""},
        ])
        with self.assertRaises(SchemaError):
            validate_programs_df(df, "test")

    def test_missing_required_column_fails_loudly(self):
        df = pd.DataFrame([{"program_id": "x"}])
        with self.assertRaises(SchemaError):
            validate_programs_df(df, "test")


class UsBackendWoodlandsMissionTests(unittest.TestCase):
    """Regression test for the real-world test case documented in
    grant_funding_programs.md/grant_readiness_score.md - locks in the
    known eligibility outcomes so a future change to the CSV or the
    matching logic can't silently flip them without a test failing."""

    @classmethod
    def setUpClass(cls):
        cls.matches = us_backend.match_grant_programs(
            org_type="nonprofit_501c3",
            sector_tags=["hazard_mitigation", "disaster_recovery", "green_infrastructure"],
            recipient_loc_id="USA-NC-021",
            has_government_partner=True,
            has_match_funding=False,
        )
        cls.by_id = {m["program_id"]: m for m in cls.matches}

    def test_returns_matches(self):
        self.assertGreater(len(self.matches), 0)

    def test_bric_blocked_on_match_funding_not_intermediary(self):
        # BRIC requires both a government intermediary AND match funding.
        # has_government_partner=True should satisfy the intermediary
        # requirement (pathway, not blocker) while still blocking on the
        # unconfirmed match funding - this is the exact distinction the
        # hardening-pass bug (2026-06-25) was about.
        bric = self.by_id["fema_bric"]
        self.assertFalse(bric["directly_eligible"])
        self.assertIn("match funding", " ".join(bric["blockers"]).lower())
        self.assertIsNotNone(bric["pathway_note"])

    def test_wood_utilization_directly_eligible_no_blockers(self):
        wood = self.by_id["usfs_wood_utilization"]
        self.assertTrue(wood["directly_eligible"])
        self.assertEqual(wood["blockers"], [])

    def test_results_sorted_eligible_first(self):
        eligible_flags = [m["directly_eligible"] for m in self.matches]
        first_blocked_index = next((i for i, e in enumerate(eligible_flags) if not e), len(eligible_flags))
        self.assertTrue(all(eligible_flags[:first_blocked_index]))


@requires_country_data("USA")
class UsBackendScoringTests(unittest.TestCase):
    def test_score_is_bounded_0_to_100(self):
        result = us_backend.score_and_rank_programs(
            org_type="nonprofit_501c3",
            sector_tags=["hazard_mitigation", "disaster_recovery"],
            recipient_loc_id="USA-NC-021",
            has_government_partner=True,
            top_n=5,
            live_opportunity_fallback=False,
        )
        for r in result["top_matches"]:
            self.assertGreaterEqual(r["score"], 0)
            self.assertLessEqual(r["score"], 100)

    def test_top_n_negative_does_not_slice_from_end(self):
        # Real bug found 2026-06-26: top_n=-1 silently returned all-but-
        # the-last result via Python's negative-index slice semantics.
        result = us_backend.score_and_rank_programs(
            org_type="nonprofit_501c3",
            top_n=-1,
            live_opportunity_fallback=False,
        )
        self.assertEqual(len(result["top_matches"]), 1)

    def test_venezuela_country_precedent_matches_known_real_numbers(self):
        # Locks in the real, documented numbers from the international-
        # track validation (2026-06-26) - 65 awards, $535.8M total.
        precedent = us_backend.get_country_precedent("98.001", "VEN")
        self.assertTrue(precedent["found"])
        self.assertEqual(precedent["award_count"], 65)
        self.assertAlmostEqual(precedent["total_obligated_amount"], 535810123.0, delta=1.0)

    def test_unknown_match_funding_is_not_silently_ok(self):
        # Direct test of the tri-state fix: a program with requires_match_
        # funding=UNKNOWN must show up as "unknown" status with an action
        # item, never as a clean "ok, no blocker."
        sub = us_backend._sub_match_funding(None, has_match_funding=False)
        self.assertEqual(sub["status"], "unknown")
        self.assertIsNotNone(sub["action_item"])
        self.assertLess(sub["points"], sub["max"])  # not full credit


class UsBackendPriorAwardsRobustnessTests(unittest.TestCase):
    def test_empty_org_name_does_not_match_everything(self):
        # Real bug found 2026-06-26: re.escape("") produced a pattern that
        # matched every row, returning has_prior_awards=True for a blank
        # field - the strongest possible signal for the weakest possible input.
        result = us_backend.check_prior_federal_awards("")
        self.assertFalse(result["has_prior_awards"])

    @requires_country_data("USA")
    def test_false_positive_name_is_surfaced_not_hidden(self):
        # "Heart to Heart" is documented to match an unrelated org
        # ("HEART TO HEART INTERNATIONAL INC") - confirm matched_recipient_
        # names surfaces this rather than silently trusting the count.
        result = us_backend.check_prior_federal_awards("Heart to Heart")
        if result["has_prior_awards"]:
            self.assertIn("matched_recipient_names", result)
            self.assertGreater(len(result["matched_recipient_names"]), 0)


class CanBackendBcWildfireTests(unittest.TestCase):
    """Regression test for the real BC wildfire/flood/forest test case
    (Canada%20grant%20research.md, 2026-06-29)."""

    @classmethod
    def setUpClass(cls):
        cls.matches = can_backend.match_can_grant_programs(
            org_type="nonprofit",
            sector_tags=["wildfire_management", "flood_mitigation", "community_resilience"],
            province="BC",
        )
        cls.by_id = {m["program_id"]: m for m in cls.matches}

    def test_returns_matches(self):
        self.assertGreater(len(self.matches), 0)

    def test_community_resilience_fund_is_not_a_disaster_match(self):
        # Public Safety Canada's Community Resilience Fund counters
        # radicalization to violence (checked 2026-10-22). It was tagged as
        # disaster resilience from its name and topped wildfire shortlists.
        self.assertNotIn("can_psc_community_resilience_fund", self.by_id)

    def test_matches_carry_lifecycle_status(self):
        self.assertTrue(all(m.get("lifecycle_status") for m in self.matches))
        self.assertEqual(self.by_id["can_ps_red_cross_flood_fire"]["lifecycle_status"], "closed")

    def test_every_match_flags_unknown_match_funding(self):
        # Every row in canada_grant_funding_programs.csv has requires_
        # match_funding=UNKNOWN today - every match should carry the
        # unknown_requirements note, not silently read as confirmed-ok.
        for m in self.matches:
            self.assertEqual(m["eligibility_confidence"], "uncertain")
            self.assertTrue(any("match funding" in u.lower() for u in m["unknown_requirements"]))

    def test_firesmart_flagged_government_intermediary_blocker(self):
        # can_nrcan_firesmart is the one row flagged TRUE for requires_
        # government_intermediary - confirm it actually blocks without a
        # government partner.
        firesmart = self.by_id.get("can_nrcan_firesmart")
        if firesmart is not None:
            self.assertFalse(firesmart["directly_eligible"])

    def test_bc_local_government_gets_ubcm_pathways(self):
        matches = can_backend.match_can_grant_programs(
            org_type="government",
            sector_tags=["wildfire_management", "flood_mitigation", "community_resilience"],
            province="BC",
        )
        by_id = {m["program_id"]: m for m in matches}
        self.assertIn("can_bc_ubcm_cepf", by_id)
        self.assertIn("can_bc_ubcm_cri", by_id)
        self.assertTrue(by_id["can_bc_ubcm_cepf"]["directly_eligible"])
        self.assertTrue(by_id["can_bc_ubcm_cri"]["directly_eligible"])

    def test_bc_scope_excludes_non_bc_projects(self):
        matches = can_backend.match_can_grant_programs(
            org_type="government",
            sector_tags=["wildfire_management", "community_resilience"],
            province="ON",
        )
        ids = {m["program_id"] for m in matches}
        self.assertNotIn("can_bc_ubcm_cepf", ids)
        self.assertNotIn("can_bc_ubcm_cri", ids)


class CanBackendAcademicPathwayTests(unittest.TestCase):
    def test_academic_data_climate_project_gets_research_pathways(self):
        matches = can_backend.match_can_grant_programs(
            org_type="academia",
            sector_tags=["research_software", "data_infrastructure", "climate_adaptation"],
            province="BC",
            has_match_funding=True,
        )
        ids = {m["program_id"] for m in matches}
        self.assertIn("can_nserc_discovery", ids)
        self.assertIn("can_nserc_alliance_advantage", ids)
        self.assertIn("can_nserc_alliance_society", ids)
        self.assertIn("can_nfrf", ids)
        self.assertIn("can_mitacs_accelerate", ids)
        self.assertIn("can_bc_bckdf", ids)


@requires_country_data("CAN")
class CanBackendPrecedentTests(unittest.TestCase):
    def test_dfaa_merges_known_real_totals(self):
        # Locks in the real numbers confirmed during the eligibility-table
        # research: 4 name variants, 72 awards, $1.7975B total.
        precedent = can_backend.get_can_precedent("can_ps_dfaa")
        self.assertTrue(precedent["found"])
        self.assertEqual(precedent["matched_variant_count"], 4)
        self.assertEqual(precedent["award_count"], 72)
        self.assertAlmostEqual(precedent["total_agreement_value"], 1797501190.89, delta=1.0)

    def test_unknown_program_id_returns_found_false_not_an_exception(self):
        result = can_backend.get_can_precedent("not_a_real_program_id")
        self.assertFalse(result["found"])


class CapabilityFlagTests(unittest.TestCase):
    def test_can_backend_capabilities_are_honest(self):
        self.assertTrue(can_backend.CAPABILITIES["discovery"])
        self.assertTrue(can_backend.CAPABILITIES["precedent"])
        self.assertFalse(can_backend.CAPABILITIES["readiness_scoring"])
        self.assertFalse(can_backend.CAPABILITIES["writing_template"])
        self.assertFalse(can_backend.CAPABILITIES["live_opportunity_check"])

    def test_unsupported_can_functions_raise_not_silently_empty(self):
        # The whole point of explicit capability flags: calling an
        # unsupported function must be loud, never a confident-looking
        # empty/zero result that's indistinguishable from "checked, found
        # nothing."
        with self.assertRaises(NotImplementedError):
            can_backend.score_and_rank_programs(org_type="nonprofit")
        with self.assertRaises(NotImplementedError):
            can_backend.check_open_opportunity("can_ps_dfaa")
        with self.assertRaises(NotImplementedError):
            can_backend.get_grant_writing_template("can_ps_dfaa")

    def test_coordinator_capabilities_match_backends(self):
        self.assertEqual(grant_analyzer.CAPABILITIES["US"], us_backend.CAPABILITIES)
        self.assertEqual(grant_analyzer.CAPABILITIES["CAN"], can_backend.CAPABILITIES)

    def test_supports_helper(self):
        self.assertTrue(grant_analyzer.supports("US", "readiness_scoring"))
        self.assertFalse(grant_analyzer.supports("CAN", "readiness_scoring"))

    def test_unknown_country_raises(self):
        with self.assertRaises(ValueError):
            grant_analyzer.get_backend("MX")


class ArcCfdaMappingTests(unittest.TestCase):
    """ARC is a federal funder, so its rows must carry CFDA numbers.

    Without one, score_and_rank_programs leaves `precedent` None and the
    recipient-type sub-factors short-circuit to "unknown" - the whole ARC
    portfolio scored with no precedent at all, which matters because ARC is
    the primary regional funder for Appalachian applicants.
    """

    @classmethod
    def setUpClass(cls):
        cls.programs = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)
        cls.arc = cls.programs[cls.programs["program_id"].str.startswith("arc_", na=False)]

    def test_every_arc_row_has_a_cfda_number(self):
        missing = [
            row["program_id"]
            for _, row in self.arc.iterrows()
            if pd.isna(row["cfda_number"]) or not str(row["cfda_number"]).strip()
        ]
        self.assertEqual(missing, [], f"ARC rows missing cfda_number: {missing}")

    @requires_country_data("USA")
    def test_arc_cfda_codes_exist_in_the_award_summary(self):
        """A mapped CFDA that isn't in the summary parquet yields no precedent,
        which is the same silent hole as having no CFDA at all."""
        summary = us_backend._load_cfda_summary_cached()
        known = set(summary["cfda_code"].astype(str))
        for _, row in self.arc.iterrows():
            for code in str(row["cfda_number"]).split("|"):
                code = code.strip()
                with self.subTest(program=row["program_id"], cfda=code):
                    self.assertIn(code, known)

    @requires_country_data("USA")
    def test_arc_area_development_resolves_real_precedent(self):
        precedent = us_backend.get_grant_precedents("23.002", org_type="nonprofit_501c3")
        self.assertTrue(precedent.get("found"))
        self.assertGreater(precedent.get("award_count") or 0, 1000)

    def test_local_development_district_matches_special_district_government(self):
        """CFDA 23.009 (Appalachian LDD Assistance) is 288 of 288 awards to
        SPECIAL DISTRICT GOVERNMENT - that is how LDDs actually register."""
        self.assertTrue(
            us_backend._org_type_matches_entry(
                "local_development_district", "SPECIAL DISTRICT GOVERNMENT (288)"
            )
        )

    @requires_country_data("USA")
    def test_arc_programs_are_scored_for_an_appalachian_nonprofit(self):
        scored = us_backend.score_and_rank_programs(
            org_type="nonprofit_501c3",
            sector_tags=["disaster_recovery", "economic_development", "workforce"],
            recipient_loc_id="USA-NC-021",
            place_of_performance_loc_id="USA-NC-021",
            has_government_partner=True,
            top_n=40,
            live_opportunity_fallback=False,
        )
        arc_ids = {
            m["program_id"] for m in scored["top_matches"]
            if str(m["program_id"]).startswith("arc_")
        }
        self.assertTrue(arc_ids, "no ARC programs scored for an Appalachian nonprofit")


class ProposalSectionTests(unittest.TestCase):
    """Universal section vocabulary, extraction, and per-funder requirements."""

    WOODLANDS = (
        "Woodlands Project: Initiatives and Grant Opportunities\n"
        "About\n"
        "Heart to Heart Foundation, a nonprofit 501c3 organization, operating in "
        "collaboration with local nonprofits, officials, and academia across the region.\n"
        "Contact: Velma Anne Ruth, President, velma@example.org\n"
        "Background\n"
        "Hurricane Helene downed millions of trees, which are drying out and increasing "
        "wildfire risk and environmental hazards, damaging infrastructure and livelihoods.\n"
        "Forest Areas - Project Locations\n"
        "Buncombe, Yancey and Avery Counties North Carolina; Grayson County Virginia.\n"
        "Collaboration\n"
        "Landscape: Identify at-risk locations in need of forest management. Extraction: "
        "Working with local loggers to extract downed trees and coordinate trucking.\n"
        "Budget Considerations\n"
        "Rates: 1300 - 1500 dollars per day per location. Expenses: Equipment and Fuel.\n"
        "Funding Requests\n"
        "Site Surveys. Contractors. Sponsorship to cover costs for professional loggers.\n"
    )

    def setUp(self):
        self.extracted = proposal_sections.extract_sections(self.WOODLANDS)

    def test_plural_heading_is_detected(self):
        """A literal 'Funding Requests' heading must not read as missing - the
        original \\brequest\\b pattern failed against the plural."""
        self.assertNotEqual(
            self.extracted["funding_request"]["status"], proposal_sections.STATUS_MISSING
        )

    def test_title_falls_back_to_first_line(self):
        """Proposals rarely label their title, so requiring the word 'Title'
        reported a missing title on documents whose first line was the title."""
        entry = self.extracted["title"]
        self.assertNotEqual(entry["status"], proposal_sections.STATUS_MISSING)
        self.assertTrue(any("Woodlands" in e for e in entry["evidence"]))

    def test_missing_section_is_reported_as_missing(self):
        """This brief genuinely has no measurable outcomes - the checklist has
        to be able to say so, or it is not worth running."""
        self.assertEqual(
            self.extracted["outcomes"]["status"], proposal_sections.STATUS_MISSING
        )

    def test_every_section_gets_a_verdict(self):
        for section in proposal_sections.PROPOSAL_SECTIONS:
            with self.subTest(section=section["id"]):
                self.assertIn(section["id"], self.extracted)

    def test_match_requirement_adds_match_section(self):
        base = {"funder_type": "federal", "requires_match_funding": "FALSE"}
        with_match = {"funder_type": "federal", "requires_match_funding": "TRUE"}
        self.assertNotIn("match_funding", proposal_sections.required_sections_for_program(base))
        self.assertIn("match_funding", proposal_sections.required_sections_for_program(with_match))

    def test_funder_types_require_different_sections(self):
        federal = set(proposal_sections.required_sections_for_program({"funder_type": "federal"}))
        foundation = set(
            proposal_sections.required_sections_for_program({"funder_type": "private_foundation"})
        )
        self.assertIn("compliance_registration", federal)
        self.assertNotIn("compliance_registration", foundation)
        self.assertIn("sustainability", foundation)

    def test_verified_agency_template_wins_over_universal_default(self):
        """Only 10 of 127 programs have a template. Where an agency published
        one, this module must defer to it rather than assert its own list."""
        programs = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)
        row = programs[programs["program_id"] == "fema_bric"].iloc[0].to_dict()
        template = us_backend.get_grant_writing_template("fema_bric")
        resolved = proposal_sections.resolve_requirements(row, template)
        self.assertEqual(resolved["source"], "agency_template_verified")
        self.assertTrue(resolved["agency_verified"])
        # Parsed from a real list, not a stringified one.
        self.assertIn("Scope of Work", resolved["agency_section_names"])

    def test_program_without_template_falls_back_to_universal(self):
        programs = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)
        row = programs[programs["program_id"] == "treasury_cdfi_erp"].iloc[0].to_dict()
        resolved = proposal_sections.resolve_requirements(row, None)
        self.assertEqual(resolved["source"], "universal_plus_funder_type_default")
        self.assertFalse(resolved["agency_verified"])
        self.assertIn("summary", resolved["universal_sections"])


class ProgramReadinessValidatorTests(unittest.TestCase):
    """validate_program.py is the executable form of the import contract. The
    live catalog must stay ERROR-free, or a program the MCP cannot use has
    landed in it."""

    def setUp(self):
        import validate_program
        self.vp = validate_program
        self.df = pd.read_csv(us_backend.PROGRAMS_CSV, dtype=str)
        self.org_types = validate_program._known_org_types(self.df)
        self.sector_tags = validate_program._known_sector_tags(self.df)
        self.template_ids = validate_program._template_program_ids()

    def _check(self, row):
        return self.vp.check_row(row, self.org_types, self.sector_tags, self.template_ids)

    def test_live_catalog_has_no_readiness_errors(self):
        errors = []
        for row in self.df.to_dict("records"):
            for level, msg in self._check(row):
                if level == "ERROR":
                    errors.append(f"{row.get('program_id')}: {msg}")
        self.assertEqual(errors, [], "MCP-unready program row(s) in the catalog")

    def test_bad_geography_scope_is_an_error(self):
        row = self.df[self.df["program_id"] == "usfs_community_wildfire_defense"].iloc[0].to_dict()
        row["geography_scope"] = "narnia"
        levels = {lvl for lvl, _ in self._check(row)}
        self.assertIn("ERROR", levels)

    def test_bad_access_path_is_an_error(self):
        row = self.df[self.df["program_id"] == "usfs_community_wildfire_defense"].iloc[0].to_dict()
        row["access_path"] = "sometimes"
        self.assertTrue(any(lvl == "ERROR" and "access_path" in msg for lvl, msg in self._check(row)))

    def test_unnormalized_org_type_is_flagged(self):
        """The exact-match gate: a bare 'nonprofit' is a singleton token here
        and must be flagged rather than silently matching nothing."""
        row = self.df[self.df["program_id"] == "usfs_community_wildfire_defense"].iloc[0].to_dict()
        row["eligible_applicant_types"] = "nonprofit"
        self.assertTrue(any(lvl == "WARN" and "nonprofit" in msg for lvl, msg in self._check(row)))


if __name__ == "__main__":
    unittest.main()
