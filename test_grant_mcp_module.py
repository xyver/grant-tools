import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grant_mcp_module as gm  # noqa: E402


class GrantMcpModuleTests(unittest.TestCase):
    def setUp(self):
        self._prior_grant_data_mode = os.environ.get("GRANT_MCP_DATA_MODE")
        os.environ["GRANT_MCP_DATA_MODE"] = "local"
        gm.reset_sessions()

    def tearDown(self):
        if self._prior_grant_data_mode is None:
            os.environ.pop("GRANT_MCP_DATA_MODE", None)
        else:
            os.environ["GRANT_MCP_DATA_MODE"] = self._prior_grant_data_mode

    def test_intake_creates_session_and_infers_phase_one(self):
        result = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit 501c3 working on wildfire debris removal, "
                "wood products reuse, and disaster recovery in Appalachia."
            ),
            profile_overrides={
                "recipient_loc_id": "USA-NC",
                "place_of_performance_loc_id": "USA-NC",
            },
        )
        self.assertEqual(result["outcome"], gm.OUTCOME_OK)
        self.assertEqual(result["current_phase"], gm.PHASE_1)
        self.assertTrue(result["session_id"].startswith("gs_"))
        self.assertEqual(result["country_code"], "US")
        self.assertEqual(result["project_profile"]["org_type"]["value"], "nonprofit_501c3")

    def test_out_of_scope_request_is_rejected_at_intake(self):
        result = gm.grant_intake_or_update_project(
            project_input="Help me with post-award compliance reporting and closeout."
        )
        self.assertEqual(result["outcome"], gm.OUTCOME_OUT_OF_SCOPE)

    def test_phase_one_analysis_returns_shortlist(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit 501c3 working on wildfire debris removal, "
                "hazard mitigation, and forest recovery in Appalachia with a government partner."
            ),
            profile_overrides={
                "recipient_loc_id": "USA-NC",
                "place_of_performance_loc_id": "USA-NC",
                "has_government_partner": True,
                "has_match_funding": False,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        self.assertEqual(result["outcome"], gm.OUTCOME_OK)
        self.assertEqual(result["phase_run"], gm.PHASE_1)
        self.assertTrue(result["top_matches"])

    def test_phase_one_top_n_is_requestable_and_bounded(self):
        intake = gm.grant_intake_or_update_project(
            project_input="We are a nonprofit working on resilience in North Carolina.",
            profile_overrides={
                "org_type": "nonprofit_501c3",
                "sector_tags": ["hazard_mitigation", "disaster_recovery"],
                "recipient_loc_id": "USA-NC",
                "place_of_performance_loc_id": "USA-NC",
                "has_government_partner": True,
                "has_match_funding": False,
            },
        )
        one = gm.grant_analyze_current_phase(session_id=intake["session_id"], top_n=1)
        self.assertEqual(len(one["top_matches"]), 1)

        bounded = gm.grant_analyze_current_phase(session_id=intake["session_id"], top_n=100)
        self.assertLessEqual(len(bounded["top_matches"]), gm.PHASE_1_MAX_TOP_N)

    def test_phase_one_california_adds_state_evidence_separate_from_top_matches(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit 501c3 working on California wildfire and "
                "climate resilience projects with a government partner."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "nonprofit_501c3",
                "sector_tags": ["wildfire", "climate_adaptation"],
                "recipient_loc_id": "USA-CA",
                "place_of_performance_loc_id": "USA-CA",
                "has_government_partner": True,
                "has_match_funding": False,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        layers = {item.get("source_layer") for item in result.get("regional_evidence") or []}
        self.assertIn("state_open_opportunity", layers)
        self.assertIn("state_award_history", layers)
        self.assertTrue(result["top_matches"])
        self.assertTrue(any("California state evidence" in item for item in result["limitations"]))

    def test_phase_one_california_shelter_access_surfaces_curated_state_programs(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "California university research project building disadvantaged "
                "community shelter access analysis, wildfire evacuation planning, "
                "road network travel time to shelters, emergency management "
                "decision-support software, and community resilience metrics."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "higher_ed",
                "recipient_loc_id": "USA-CA",
                "place_of_performance_loc_id": "USA-CA",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        california_evidence = next(
            item for item in result.get("regional_evidence") or []
            if item.get("source_name") == "California curated eligibility rows"
        )
        ids = {item.get("program_id") for item in california_evidence.get("programs") or []}
        self.assertIn("cec_epic", ids)
        self.assertIn("caltrans_stpg", ids)
        self.assertIn("calfire_wildfire_prevention", ids)
        self.assertIn("sgc_crc", ids)
        self.assertIn("calfire_forest_health_research", ids)
        statuses = {
            item.get("program_id"): item.get("program_status")
            for item in california_evidence.get("programs") or []
        }
        self.assertEqual(statuses.get("calfire_wildfire_prevention"), "deadline_immediate")
        self.assertEqual(statuses.get("lci_apgp"), "watchlist_closed")
        self.assertIn("match_california_curated_programs", result["selected_workflow_steps"])

    def test_phase_one_appalachia_adds_arc_live_evidence(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a local government in Appalachia seeking economic "
                "development implementation funding."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "local_government",
                "sector_tags": ["economic_development"],
                "recipient_loc_id": "USA-WV",
                "place_of_performance_loc_id": "USA-WV",
                "has_government_partner": True,
                "has_match_funding": False,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        layers = {item.get("source_layer") for item in result.get("regional_evidence") or []}
        self.assertIn("live_announcement", layers)
        self.assertTrue(any("ARC live announcements" in item for item in result["limitations"]))

    def test_phase_one_appalachia_infrastructure_surfaces_expanded_arc_rows(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a local government in Appalachia planning broadband, "
                "road access, water infrastructure, workforce, and food system "
                "upgrades with a state partner and match funding."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "local_government",
                "recipient_loc_id": "USA-WV",
                "place_of_performance_loc_id": "USA-WV",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        arc_evidence = next(
            item for item in result.get("regional_evidence") or []
            if item.get("source_layer") == "regional_curated_programs"
        )
        ids = {item.get("program_id") for item in arc_evidence.get("programs") or []}
        self.assertTrue({
            "arc_area_development",
            "arc_broadband",
            "arc_local_access_roads",
        } & ids)

    def test_phase_one_appalachia_workforce_surfaces_arc_worc_or_arise(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit in Appalachia building a multi-state "
                "workforce and economic development partnership for displaced "
                "workers and small business entrepreneurship."
            ),
            country_code="US",
            profile_overrides={
                "recipient_loc_id": "USA-WV",
                "place_of_performance_loc_id": "USA-WV",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        arc_evidence = next(
            item for item in result.get("regional_evidence") or []
            if item.get("source_layer") == "regional_curated_programs"
        )
        ids = {item.get("program_id") for item in arc_evidence.get("programs") or []}
        self.assertTrue({"arc_worc", "arc_arise", "arc_access_to_capital"} & ids)

    def test_phase_one_northern_virginia_does_not_add_arc_live_evidence(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "George Mason University research project in Fairfax, Virginia "
                "building geospatial climate risk and disaster resilience software."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "higher_ed",
                "sector_tags": ["climate_adaptation", "research", "geospatial_analytics"],
                "recipient_loc_id": "USA-VA-059",
                "place_of_performance_loc_id": "USA-VA-059",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        layers = {item.get("source_layer") for item in result.get("regional_evidence") or []}
        self.assertNotIn("live_announcement", layers)
        self.assertFalse(any("ARC live announcements" in item for item in result["limitations"]))
        self.assertEqual(result["localization_summary"]["primary_filter_loc_id"], "USA-VA-059")
        self.assertIn("virginia", result["localization_summary"]["regional_source_scopes"])
        self.assertNotIn("appalachia", result["localization_summary"]["regional_source_scopes"])

    def test_phase_one_gmu_commercialization_surfaces_virginia_programs(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "George Mason University researchers in Virginia are building "
                "geospatial public safety software for emergency responders and "
                "want proof of concept, prototype, commercialization, and SBIR "
                "support."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "higher_ed",
                "recipient_loc_id": "USA-VA-059",
                "place_of_performance_loc_id": "USA-VA-059",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        virginia_evidence = next(
            item for item in result.get("regional_evidence") or []
            if item.get("source_name") == "Virginia Innovation Partnership Corporation curated eligibility rows"
        )
        ids = {item.get("program_id") for item in virginia_evidence.get("programs") or []}
        self.assertIn("vipc_he_poc", ids)
        self.assertIn("vipc_public_safety_innovation", ids)

    def test_intake_resolves_location_text_overrides_to_loc_ids(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "A university research software project for climate risk and "
                "public safety."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "higher_ed",
                "recipient_location_text": "Fairfax County, Virginia",
                "project_area_text": "Fairfax County, Virginia",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        self.assertEqual(intake["outcome"], gm.OUTCOME_OK)
        self.assertEqual(intake["project_profile"]["recipient_loc_id"]["value"], "USA-VA-059")
        self.assertEqual(intake["project_profile"]["place_of_performance_loc_id"]["value"], "USA-VA-059")

    def test_intake_asks_for_location_instead_of_guessing_from_narrative(self):
        """An institution named in the narrative is a collaborator, not a place
        of performance. Guessing from prose relocated a western-NC disaster
        project to Fairfax County because it mentioned a GMU partner, so intake
        now asks instead of inferring."""
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "GMU researchers are building proof of concept geospatial "
                "public safety software for emergency responders."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "higher_ed",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        self.assertEqual(intake["outcome"], gm.OUTCOME_CLARIFICATION)
        self.assertIn("place_of_performance_loc_id", intake["project_profile_missing_fields"])
        self.assertIn(
            "place_of_performance",
            {question["question_id"] for question in intake["questions"]},
        )
        self.assertIsNone(intake["project_profile"]["place_of_performance_loc_id"]["value"])

    def test_intake_accepts_multiple_places_of_performance(self):
        """A coalition working across several counties is scored in each one,
        because eligibility differs by location."""
        intake = gm.grant_intake_or_update_project(
            project_input="Nonprofit clearing storm debris and reducing wildfire fuel.",
            country_code="US",
            profile_overrides={
                "org_type": "nonprofit",
                "recipient_location_text": "Buncombe County, North Carolina",
                "project_area_text": (
                    "Buncombe County, North Carolina; Yancey County, North Carolina; "
                    "Grayson County, Virginia"
                ),
                "has_government_partner": True,
            },
        )
        self.assertEqual(intake["outcome"], gm.OUTCOME_OK)
        self.assertEqual(
            intake["project_profile"]["project_area_loc_ids"]["value"],
            ["USA-NC-021", "USA-NC-199", "USA-VA-077"],
        )
        # org_type is snapped onto the backend's exact-match vocabulary.
        self.assertEqual(intake["project_profile"]["org_type"]["value"], "nonprofit_501c3")

    def test_unreachable_programs_are_split_out_of_the_shortlist(self):
        """A program can be the right applicant type and still have no way to
        submit - FEMA PA has no application, ARC ARISE needs an invitation.
        Those must not sit in the shortlist looking like options."""
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "Nonprofit disaster recovery clearing storm debris, wildfire fuel "
                "reduction, wood reuse, and workforce development."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "nonprofit_501c3",
                "recipient_loc_id": "USA-NC-021",
                "project_area_loc_ids": ["USA-NC-021", "USA-NC-199"],
                "has_government_partner": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"], depth="full")

        blocked = result["no_application_path"]
        self.assertTrue(blocked, "expected at least one unreachable program to be reported")
        for entry in blocked:
            with self.subTest(program=entry["program_id"]):
                self.assertIn(entry["access_path"], gm.UNREACHABLE_ACCESS_PATHS)
                self.assertTrue(entry["why"], "an excluded program must say why")

        shortlist_ids = {m["program_id"] for m in result["top_matches"]}
        blocked_ids = {m["program_id"] for m in blocked}
        self.assertFalse(shortlist_ids & blocked_ids, "unreachable program leaked into shortlist")
        for match in result["top_matches"]:
            self.assertNotIn(match.get("access_path"), gm.UNREACHABLE_ACCESS_PATHS)

    def test_closed_programs_move_to_historical_precedent(self):
        """A one-time 2012 appropriation scored 85 and sat in the shortlist.
        It is worthless as a target and useful as precedent, so it is
        relabelled rather than dropped."""
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "Nonprofit disaster recovery clearing storm debris and restoring "
                "damaged habitat after a hurricane."
            ),
            country_code="US",
            profile_overrides={
                "org_type": "nonprofit_501c3",
                "recipient_loc_id": "USA-NC-021",
                "project_area_loc_ids": ["USA-NC-021", "USA-NC-199"],
                "has_government_partner": True,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"], depth="full")

        historical = result["historical_precedent"]
        self.assertTrue(historical, "expected at least one historical program to be reported")
        ids = {m["program_id"] for m in historical}
        self.assertIn("doi_hurricane_sandy_relief_fws", ids)
        for entry in historical:
            with self.subTest(program=entry["program_id"]):
                self.assertIn(entry["lifecycle_status"], gm.HISTORICAL_LIFECYCLE_STATUSES)
                self.assertTrue(entry["why_not_a_target"])

        shortlist_ids = {m["program_id"] for m in result["top_matches"]}
        self.assertFalse(shortlist_ids & ids, "closed program leaked into the shortlist")

    def test_recurring_and_unknown_stay_applicable(self):
        """A recurring program between rounds is still a target, and unknown
        means undetermined - neither may be filed as historical."""
        self.assertNotIn("recurring", gm.HISTORICAL_LIFECYCLE_STATUSES)
        self.assertNotIn("unknown", gm.HISTORICAL_LIFECYCLE_STATUSES)

    def test_loi_required_stays_in_the_shortlist(self):
        """An LOI is a first step, not a closed door - it must not be filtered
        out the way an invitation-only program is."""
        self.assertNotIn("loi_required", gm.UNREACHABLE_ACCESS_PATHS)

    def test_unresearched_access_path_is_not_treated_as_blocked(self):
        """Blank access_path means nobody has checked, not that the program is
        unreachable. Treating it as blocked would hide most of the catalog."""
        self.assertNotIn(None, gm.UNREACHABLE_ACCESS_PATHS)
        self.assertNotIn("", gm.UNREACHABLE_ACCESS_PATHS)

    def test_intake_normalizes_org_type_synonyms(self):
        """match_grant_programs() gates on an exact string match, so an
        unnormalized 'nonprofit' silently matched zero of 127 programs."""
        for supplied in ("nonprofit", "501c3", "Non-Profit", "NONPROFIT_501C3"):
            with self.subTest(org_type=supplied):
                intake = gm.grant_intake_or_update_project(
                    project_input="Storm debris removal and wood reuse.",
                    country_code="US",
                    profile_overrides={
                        "org_type": supplied,
                        "project_area_loc_ids": ["USA-NC-021"],
                        "recipient_loc_id": "USA-NC-021",
                    },
                )
                self.assertEqual(
                    intake["project_profile"]["org_type"]["value"], "nonprofit_501c3"
                )

    def test_heartland_agtech_text_infers_grant_tags(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a university and startup partnership in North Dakota "
                "working on agtech field trials, agriculture resilience, farm "
                "data infrastructure, and rural community upgrades."
            ),
            country_code="US",
            profile_overrides={
                "recipient_loc_id": "USA-ND",
                "place_of_performance_loc_id": "USA-ND",
                "has_government_partner": True,
                "has_match_funding": True,
            },
        )
        tags = set(intake["project_profile"]["sector_tags"]["value"])
        self.assertIn("food_security", tags)
        self.assertIn("rural_economic_development", tags)
        self.assertIn("innovation_ecosystem", tags)

    def test_phase_one_bc_adds_provincial_award_evidence(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a BC nonprofit working on wildfire resilience and "
                "community preparedness."
            ),
            country_code="CAN",
            profile_overrides={
                "org_type": "nonprofit",
                "sector_tags": ["wildfire_management", "community_resilience"],
                "recipient_loc_id": "CAN-BC",
                "place_of_performance_loc_id": "CAN-BC",
                "has_government_partner": True,
                "has_match_funding": False,
            },
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        layers = {item.get("source_layer") for item in result.get("regional_evidence") or []}
        self.assertIn("provincial_award_history", layers)
        self.assertTrue(any("BC provincial award evidence" in item for item in result["limitations"]))

    def test_phase_one_canadian_academic_query_surfaces_academic_pathways(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a university research lab in British Columbia building "
                "a climate data science and geospatial research software "
                "platform for wildfire and flood resilience."
            ),
            country_code="CAN",
            profile_overrides={
                "recipient_loc_id": "CAN-BC",
                "place_of_performance_loc_id": "CAN-BC",
                "has_match_funding": True,
            },
        )
        self.assertEqual(
            intake["project_profile"]["org_type"]["value"],
            "academia",
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        ids = [item.get("program_id") for item in result.get("top_matches") or []]
        self.assertIn("can_nserc_discovery", ids)
        self.assertIn("can_nserc_alliance_advantage", ids)
        self.assertIn("can_mitacs_accelerate", ids)
        self.assertIn("can_bc_bckdf", ids)
        open_ids = [item.get("program_id") for item in result.get("open_now_summary") or []]
        self.assertIn("can_nserc_discovery", open_ids)
        self.assertTrue(all(
            item.get("opportunity_status") == "program_page_check_required"
            for item in result.get("open_now_summary") or []
        ))

    def test_phase_one_bc_government_query_surfaces_ubcm_pathways(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a municipal local government in British Columbia "
                "seeking funding for wildfire resilience, emergency "
                "preparedness, flood mitigation, and community resilience."
            ),
            country_code="CAN",
            profile_overrides={
                "recipient_loc_id": "CAN-BC",
                "place_of_performance_loc_id": "CAN-BC",
            },
        )
        self.assertEqual(
            intake["project_profile"]["org_type"]["value"],
            "government",
        )
        result = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        ids = [item.get("program_id") for item in result.get("top_matches") or []]
        self.assertEqual(ids[:2], ["can_bc_ubcm_cepf", "can_bc_ubcm_cri"])

    def test_phase_two_without_selected_program_clarifies(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit 501c3 working on wildfire debris removal "
                "and disaster recovery in Appalachia."
            ),
            profile_overrides={
                "recipient_loc_id": "USA-NC",
                "place_of_performance_loc_id": "USA-NC",
                "has_government_partner": True,
            },
        )
        gm.grant_analyze_current_phase(session_id=intake["session_id"])
        result = gm.grant_analyze_current_phase(
            session_id=intake["session_id"],
            analysis_request="Turn this into memo talking points for leadership.",
        )
        self.assertEqual(result["outcome"], gm.OUTCOME_CLARIFICATION)
        self.assertEqual(result["phase_run"], gm.PHASE_2)

    def test_select_program_allows_phase_two_memo(self):
        intake = gm.grant_intake_or_update_project(
            project_input=(
                "We are a nonprofit 501c3 working on wildfire debris removal "
                "and disaster recovery in Appalachia."
            ),
            profile_overrides={
                "recipient_loc_id": "USA-NC",
                "place_of_performance_loc_id": "USA-NC",
                "has_government_partner": True,
            },
        )
        phase1 = gm.grant_analyze_current_phase(session_id=intake["session_id"])
        first_program_id = phase1["top_matches"][0]["program_id"]
        gm.grant_select_target_program(session_id=intake["session_id"], program_id=first_program_id)
        result = gm.grant_analyze_current_phase(
            session_id=intake["session_id"],
            analysis_request="Turn this into memo talking points for leadership.",
        )
        self.assertEqual(result["outcome"], gm.OUTCOME_OK)
        self.assertEqual(result["phase_run"], gm.PHASE_2)
        self.assertIsNotNone(result["memo_support"])


if __name__ == "__main__":
    unittest.main()
