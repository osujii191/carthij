import unittest
from src.core.agent import BaseAgent
from src.api.schemas import FormSubmissionRequest, SubagentTask, WorkflowExecutionResponse


class TestAgentHarness(unittest.TestCase):
    def test_form_submission_schema(self):
        payload = {
            "form_id": "onboarding_123",
            "submitter_email": "test@example.com",
            "data": {
                "company_name": "Acme Corp",
                "industry": "Fintech",
                "annual_revenue": 5000000,
            },
            "context": "Focus on regulatory compliance.",
        }
        req = FormSubmissionRequest(**payload)
        self.assertEqual(req.form_id, "onboarding_123")
        self.assertEqual(req.data["company_name"], "Acme Corp")

    def test_agent_tool_registration(self):
        def dummy_tool(query: str) -> str:
            """A test tool function."""
            return f"result for {query}"

        agent = BaseAgent(
            name="test_worker",
            role="tester",
            system_instruction="You are a test agent.",
            tools=[dummy_tool],
        )

        self.assertIn("dummy_tool", agent._tools_map)
        self.assertEqual(agent._tools_map["dummy_tool"]("hello"), "result for hello")

    def test_subagent_task_schema(self):
        task = SubagentTask(
            subagent_type="researcher",
            prompt="Analyze competitor pricing.",
            context={"competitor": "BetaCorp"},
        )
        self.assertEqual(task.subagent_type, "researcher")
        self.assertEqual(task.context["competitor"], "BetaCorp")

    def test_query_plan_schema(self):
        from src.agents.planner import PlannedQuery, QueryPlan
        pq = PlannedQuery(
            query="Empire State Development biotech grants NY",
            focus_area="state_incentives",
            scope="state",
            search_depth="advanced",
        )
        plan = QueryPlan(
            strategy="Search NY state grants followed by federal SBIR.",
            queries=[pq],
        )
        self.assertEqual(len(plan.queries), 1)
        self.assertEqual(plan.queries[0].scope, "state")

    def test_tavily_client_simulation_fallback(self):
        import asyncio
        from src.core.tavily import TavilyClient

        client = TavilyClient(api_key="")
        res = asyncio.run(client.search("NJEDA angel tax credit"))
        self.assertEqual(res.query, "NJEDA angel tax credit")
        self.assertGreaterEqual(len(res.results), 1)
        self.assertIn("Simulated search results", res.results[0].content)

    def test_tavily_request_openapi_constraints(self):
        from src.core.tavily import TavilySearchRequest

        # include_domains_mode without include_domains must be omitted;
        # country with topic='finance' must be omitted;
        # chunks_per_source and safe_search with ultra-fast must be omitted.
        req = TavilySearchRequest(
            query="seed funding climate tech",
            search_depth="ultra-fast",
            chunks_per_source=3,
            topic="finance",
            country="united states",
            include_domains=[],
            include_domains_mode="prefer",
            filter_by_language=True,
            safe_search=True,
        )
        payload = req.to_api_payload()
        self.assertNotIn("chunks_per_source", payload)
        self.assertNotIn("country", payload)
        self.assertNotIn("include_domains_mode", payload)
        self.assertNotIn("filter_by_language", payload)
        self.assertNotIn("safe_search", payload)

        # Valid combination preserves all fields
        valid_req = TavilySearchRequest(
            query="Empire State Development grants",
            search_depth="advanced",
            chunks_per_source=3,
            topic="general",
            country="United States",
            time_range="year",
            include_domains=["esd.ny.gov"],
            include_domains_mode="prefer",
            include_published_date=True,
        )
        valid_payload = valid_req.to_api_payload()
        self.assertEqual(valid_payload["chunks_per_source"], 3)
        self.assertEqual(valid_payload["country"], "united states")
        self.assertEqual(valid_payload["include_domains"], ["esd.ny.gov"])
        self.assertEqual(valid_payload["include_domains_mode"], "prefer")
        self.assertEqual(valid_payload["time_range"], "year")
        self.assertTrue(valid_payload["include_published_date"])

    def test_tavily_parallel_search_many_and_deduplication(self):
        import asyncio
        from src.agents.planner import PlannedQuery, QueryPlan, QueryPlanner
        from src.core.tavily import TavilyClient, TavilySearchResponse, TavilySearchResult

        plan = QueryPlan(
            strategy="Parallel state and federal funding lookup.",
            queries=[
                PlannedQuery(
                    query="Empire State Development biotech grants NY",
                    focus_area="state_incentives",
                    scope="state",
                    search_depth="advanced",
                    preferred_domains=["esd.ny.gov"],
                ),
                PlannedQuery(
                    query="NIH SBIR Phase I biotech grant eligibility",
                    focus_area="federal_grants",
                    scope="federal",
                    search_depth="advanced",
                    preferred_domains=["sbir.gov"],
                ),
            ],
        )

        planner = QueryPlanner(tavily_client=TavilyClient(api_key=""))
        requests = planner.build_tavily_requests(plan, max_results=4)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].include_domains_mode, "prefer")
        self.assertEqual(requests[0].country, "united states")

        responses = asyncio.run(planner.execute_plan(plan, max_results=4))
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].query, plan.queries[0].query)
        self.assertEqual(responses[1].query, plan.queries[1].query)

        # Deduplication merges duplicate URLs across queries and keeps higher score
        mock_responses = [
            TavilySearchResponse(
                query=plan.queries[0].query,
                results=[
                    TavilySearchResult(
                        title="Shared Program",
                        url="https://esd.ny.gov/grants/",
                        content="Lower score snippet",
                        score=0.72,
                    ),
                ],
            ),
            TavilySearchResponse(
                query=plan.queries[1].query,
                results=[
                    TavilySearchResult(
                        title="Shared Program Detailed",
                        url="https://esd.ny.gov/grants#eligibility",
                        content="Higher score snippet",
                        score=0.91,
                    ),
                    TavilySearchResult(
                        title="SBIR Portal",
                        url="https://www.sbir.gov/about",
                        content="Federal SBIR overview",
                        score=0.85,
                    ),
                ],
            ),
        ]
        deduped = planner.deduplicate_results(plan, mock_responses)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0].score, 0.91)
        self.assertEqual(deduped[0].content, "Higher score snippet")
        self.assertEqual(set(deduped[0].matched_scopes), {"state", "federal"})
        self.assertEqual(deduped[1].url, "https://www.sbir.gov/about")

    def test_tavily_extract_simulation_and_empty_urls(self):
        import asyncio
        from src.core.tavily import TavilyClient

        client = TavilyClient(api_key="")
        empty_res = asyncio.run(client.extract([]))
        self.assertEqual(empty_res.results, [])

        res = asyncio.run(client.extract(["https://esd.ny.gov/grants", "https://www.sbir.gov/"]))
        self.assertEqual(len(res.results), 2)
        self.assertIn("Simulated extracted markdown", res.results[0].raw_content)

    def test_canonical_report_models_and_excerpt_truncation(self):
        from src.models.report import (
            ActionItem,
            FundingOpportunity,
            FundingReport,
            ReadinessGap,
            SourceRecord,
            StageProgress,
            UNVERIFIED_PLACEHOLDER,
        )

        long_markdown = "A" * 5000
        src = SourceRecord(
            source_id="SRC-1",
            url="https://esd.ny.gov/grants",
            normalized_url="https://esd.ny.gov/grants",
            title="ESD Grants",
            extracted_content=long_markdown,
            score=0.92,
            is_extracted=True,
        )
        persisted = src.to_persisted_dict(max_excerpt_chars=2000)
        self.assertEqual(len(persisted["excerpt"]), 2003)  # 2000 + "..."
        self.assertTrue(persisted["is_extracted"])

        opp = FundingOpportunity(
            name="Empire State Innovation Grant",
            provider="Empire State Development",
            scope="state",
            instrument="Grant",
            eligibility_fit="high",
            why_it_fits="NY-based R&D startup",
            source_url="https://esd.ny.gov/grants",
            source_id="SRC-1",
            citation_verified=True,
        )
        self.assertEqual(opp.deadline, UNVERIFIED_PLACEHOLDER)

        report = FundingReport(
            report_id="rep-123",
            status="completed",
            owner_uid="uid-42",
            submitter_email="founder@acme.io",
            org_name="Acme Bio",
            org_type="Startup",
            target_state="NY",
            intake_data={"organization_name": "Acme Bio"},
            readiness_score=84,
            opportunities=[opp],
            readiness_gaps=[
                ReadinessGap(
                    category="compliance",
                    severity="medium",
                    description="SAM.gov UEI registration not yet completed.",
                    remediation="Register for UEI at SAM.gov before applying to SBIR.",
                )
            ],
            action_plan=[
                ActionItem(priority=1, timeframe="immediate_7d", action="Register on SAM.gov")
            ],
            sources=[persisted],
            stage_history=[StageProgress(stage="completed", message="Done")],
        )
        self.assertEqual(report.owner_uid, "uid-42")
        self.assertEqual(len(report.opportunities), 1)
        self.assertEqual(report.readiness_score, 84)

    def test_source_registry_concurrency_dedup_authority_and_enrichment(self):
        import asyncio
        from src.agents.planner import PlannedQuery, QueryPlan, QueryPlanner
        from src.core.tavily import TavilyClient
        from src.pipeline.registry import RetrievedSourceRegistry

        async def _run():
            registry = RetrievedSourceRegistry()
            # Concurrent registrations of overlapping URLs (www vs non-www, trailing slash, fragment)
            await asyncio.gather(
                registry.register_source(
                    url="https://www.esd.ny.gov/grants/",
                    title="ESD Grants",
                    snippet="Snippet 1",
                    score=0.70,
                    scope="state",
                ),
                registry.register_source(
                    url="https://esd.ny.gov/grants#apply",
                    title="ESD Grants Portal",
                    snippet="Higher score snippet",
                    score=0.85,
                    scope="compliance",
                ),
                registry.register_source(
                    url="https://randomblog.example.com/grants-list",
                    title="Blog Post",
                    snippet="General blog",
                    score=0.82,
                    scope="market",
                ),
            )

            self.assertEqual(len(registry), 2)
            verified = registry.verify_url("https://esd.ny.gov/grants")
            self.assertIsNotNone(verified)
            self.assertEqual(verified.score, 0.85)
            self.assertEqual(set(verified.matched_scopes), {"state", "compliance"})
            self.assertIsNone(registry.verify_url("https://hallucinated.gov/fake"))

            # Authority boost ranks esd.ny.gov above randomblog
            top_urls = registry.select_top_urls_for_extraction(limit=2)
            self.assertEqual(top_urls[0], "https://www.esd.ny.gov/grants/")

            # End-to-end execute_and_enrich with simulated Tavily client
            plan = QueryPlan(
                strategy="Test hybrid search + extract",
                queries=[
                    PlannedQuery(
                        query="NJEDA angel tax credit",
                        focus_area="state_incentives",
                        scope="state",
                        search_depth="basic",
                    )
                ],
            )
            planner = QueryPlanner(tavily_client=TavilyClient(api_key=""))
            run_registry = RetrievedSourceRegistry()
            report = await planner.execute_and_enrich(
                plan,
                enable_extract=True,
                max_extract_urls=2,
                registry=run_registry,
            )
            self.assertEqual(report.total_unique_sources, 1)
            self.assertEqual(report.extracted_urls_count, 1)
            self.assertTrue(run_registry.list_sources()[0].is_extracted)
            formatted = planner.format_search_results_for_context(
                plan, report.responses, registry=run_registry
            )
            self.assertIn("[SRC-1] [FULL EXTRACT]", formatted)

        asyncio.run(_run())

    def test_query_plan_endpoint_hidden_by_default(self):
        from src.api.app import app

        paths = [r.path for r in app.routes if hasattr(r, "path")]
        self.assertNotIn("/api/v1/query-plan", paths)

    def test_base_agent_two_phase_tools_then_schema(self):
        import asyncio
        from types import SimpleNamespace
        from pydantic import BaseModel
        from google.genai import types
        from src.core.agent import BaseAgent

        class DummySchema(BaseModel):
            verdict: str

        captured_configs = []

        class FakeModels:
            def __init__(self):
                self.calls = 0

            async def generate_content(self, *, model, contents, config):
                self.calls += 1
                captured_configs.append(config)
                if self.calls == 1:
                    # Request a tool call on turn 1
                    fc = SimpleNamespace(name="lookup_grant", args={"name": "NYSTAR"})
                    return SimpleNamespace(
                        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="calling tool")]))],
                        function_calls=[fc],
                        text="",
                    )
                if self.calls == 2:
                    # Finish tool loop without further calls
                    return SimpleNamespace(
                        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="tool done")]))],
                        function_calls=[],
                        text="tool done",
                    )
                # Phase 2 structured turn
                return SimpleNamespace(
                    candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text='{"verdict": "qualified"}')]))],
                    function_calls=[],
                    text='{"verdict": "qualified"}',
                )

        fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

        def lookup_grant(name: str) -> str:
            return f"found {name}"

        agent = BaseAgent(
            name="two_phase_tester",
            role="tester",
            system_instruction="Test two-phase execution.",
            tools=[lookup_grant],
            response_schema=DummySchema,
            client=fake_client,
        )

        result = asyncio.run(agent.run("Check NYSTAR"))
        parsed = DummySchema.model_validate_json(result)
        self.assertEqual(parsed.verdict, "qualified")
        self.assertEqual(len(captured_configs), 3)
        # Phase 1 turns have tools enabled and no response_schema
        self.assertIsNotNone(captured_configs[0].tools)
        self.assertIsNone(captured_configs[0].response_schema)
        # Phase 2 final turn has response_schema enabled and no tools
        self.assertIsNone(captured_configs[2].tools)
        self.assertEqual(captured_configs[2].response_schema, DummySchema)

    def test_bounded_tavily_tool_suite_enforces_budget(self):
        import asyncio
        from src.agents.specialists.tools import BoundedTavilyToolSuite
        from src.core.tavily import TavilyClient
        from src.pipeline.registry import RetrievedSourceRegistry

        async def _run():
            registry = RetrievedSourceRegistry()
            suite = BoundedTavilyToolSuite(
                specialist_name="state_local_programs",
                tavily_client=TavilyClient(api_key=""),
                registry=registry,
                max_calls=2,
                default_scope="state",
            )
            search_tool, extract_tool = suite.as_tools()

            r1 = await search_tool("NYSTAR grants")
            self.assertEqual(r1["status"], "ok")
            self.assertEqual(r1["calls_remaining"], 1)
            self.assertEqual(len(registry), 1)

            r2 = await extract_tool("https://example.gov/incentives")
            self.assertEqual(r2["status"], "ok")
            self.assertEqual(r2["calls_remaining"], 0)

            # 3rd call must be blocked by budget cap
            r3 = await search_tool("should not execute")
            self.assertEqual(r3["status"], "budget_exhausted")
            self.assertEqual(suite.calls_used, 2)

        asyncio.run(_run())

    def test_specialist_roster_routing(self):
        from src.agents.specialists import build_specialists
        from src.core.tavily import TavilyClient
        from src.pipeline.registry import RetrievedSourceRegistry

        client = TavilyClient(api_key="")
        reg = RetrievedSourceRegistry()

        startup_specs = build_specialists(
            org_type="Startup",
            target_state="NY",
            model="gemini-2.5-flash",
            tavily_client=client,
            registry=reg,
        )
        startup_names = [s.name for s in startup_specs]
        self.assertEqual(
            startup_names,
            [
                "state_local_programs",
                "federal_foundation_grants",
                "capital_strategy",
                "eligibility_compliance",
            ],
        )
        self.assertIsNone(startup_specs[-1].tool_suite)

        nonprofit_specs = build_specialists(
            org_type="Non-Profit",
            target_state="NJ",
            model="gemini-2.5-flash",
            tavily_client=client,
            registry=reg,
        )
        nonprofit_names = [s.name for s in nonprofit_specs]
        self.assertIn("philanthropic_strategy", nonprofit_names)
        self.assertNotIn("capital_strategy", nonprofit_names)

    def test_programmatic_citation_grounding(self):
        import asyncio
        from src.models.report import FundingOpportunity, UNVERIFIED_PLACEHOLDER
        from src.pipeline.grounding import verify_and_ground_opportunities
        from src.pipeline.registry import RetrievedSourceRegistry

        async def _run():
            reg = RetrievedSourceRegistry()
            await reg.register_source(
                url="https://esd.ny.gov/innovation-grants",
                title="ESD Innovation Grants",
                snippet="Up to $250,000 for NY startups.",
                score=0.92,
                scope="state",
            )

            candidates = [
                FundingOpportunity(
                    name="ESD Innovation Grant",
                    provider="Empire State Development",
                    scope="state",
                    instrument="Grant",
                    estimated_amount="$250,000",
                    deadline="TBD",
                    eligibility_fit="medium",
                    why_it_fits="NY startup",
                    key_requirements=["NY nexus"],
                    source_url="https://www.esd.ny.gov/innovation-grants/",
                    source_id="",
                ),
                # Duplicate from another specialist with higher fit + extra requirement
                FundingOpportunity(
                    name="ESD Innovation Grant",
                    provider="Empire State Development",
                    scope="state",
                    instrument="Grant",
                    estimated_amount="$250,000",
                    deadline="",
                    eligibility_fit="high",
                    why_it_fits="Strong match for NY R&D startup",
                    key_requirements=["Matching funds"],
                    source_url="",
                    source_id="SRC-1",
                ),
                # Hallucinated opportunity not in registry
                FundingOpportunity(
                    name="Fake Unlimited Money Grant",
                    provider="Imaginary Agency",
                    scope="federal",
                    instrument="Grant",
                    eligibility_fit="high",
                    why_it_fits="Too good to be true",
                    source_url="https://fake-agency.gov/free-money",
                    source_id="SRC-99",
                ),
            ]

            report = verify_and_ground_opportunities(candidates, reg)
            self.assertEqual(report.verified_count, 1)
            self.assertEqual(report.dropped_count, 1)
            opp = report.verified_opportunities[0]
            self.assertTrue(opp.citation_verified)
            self.assertEqual(opp.source_id, "SRC-1")
            self.assertEqual(opp.eligibility_fit, "high")
            self.assertEqual(opp.deadline, UNVERIFIED_PLACEHOLDER)
            self.assertEqual(set(opp.key_requirements), {"NY nexus", "Matching funds"})
            self.assertEqual(report.dropped_opportunities[0].name, "Fake Unlimited Money Grant")

        asyncio.run(_run())

    def test_sqlite_report_repository_atomic_progress_and_filtering(self):
        import asyncio
        import os
        import tempfile
        from src.core.repository import SQLiteReportRepository
        from src.models.report import FundingReport, StageProgress

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        async def _run():
            repo = SQLiteReportRepository(db_path=temp_db)
            rep1 = FundingReport(
                report_id="rep-a",
                status="queued",
                owner_uid="user-1",
                org_name="Alpha Bio",
                org_type="Startup",
                target_state="NY",
                stage_history=[StageProgress(stage="queued", message="Queued")],
            )
            rep2 = FundingReport(
                report_id="rep-b",
                status="queued",
                owner_uid="user-2",
                org_name="Beta NonProfit",
                org_type="Non-Profit",
                target_state="NJ",
            )
            await repo.create(rep1)
            await repo.create(rep2)

            # Concurrent stage progress appends must not lose updates
            await asyncio.gather(
                repo.append_stage_progress(
                    "rep-a",
                    StageProgress(stage="query_planning", message="Planning"),
                    status="in_progress",
                ),
                repo.append_stage_progress(
                    "rep-a",
                    StageProgress(stage="searching", message="Searching"),
                    status="in_progress",
                ),
                repo.append_stage_progress(
                    "rep-a",
                    StageProgress(stage="extracting", message="Extracting"),
                    status="in_progress",
                ),
            )

            loaded = await repo.get("rep-a")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "in_progress")
            self.assertEqual(len(loaded.stage_history), 4)

            user1_reports = await repo.list_reports(owner_uid="user-1")
            self.assertEqual(len(user1_reports), 1)
            self.assertEqual(user1_reports[0].report_id, "rep-a")

            await repo.mark_failed("rep-b", "Simulated failure")
            failed = await repo.get("rep-b")
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.stage_history[-1].stage, "failed")

        try:
            asyncio.run(_run())
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_reports_api_create_dispatch_and_sse_stream(self):
        import asyncio
        import os
        import tempfile
        from fastapi.testclient import TestClient
        import src.api.app as api_app
        from src.core.dispatcher import JobPayload, LocalAsyncDispatcher
        from src.core.repository import SQLiteReportRepository
        from src.models.report import StageProgress

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        orig_repo = api_app.report_repository
        orig_dispatcher = api_app.job_dispatcher

        try:
            test_repo = SQLiteReportRepository(db_path=temp_db)

            async def fake_worker(payload: JobPayload) -> None:
                await test_repo.append_stage_progress(
                    payload.report_id,
                    StageProgress(stage="query_planning", message="Planning queries"),
                    status="in_progress",
                )
                await test_repo.append_stage_progress(
                    payload.report_id,
                    StageProgress(stage="completed", message="Done"),
                    status="completed",
                )

            api_app.report_repository = test_repo
            api_app.job_dispatcher = LocalAsyncDispatcher(worker_fn=fake_worker)

            client = TestClient(api_app.app)
            post_res = client.post(
                "/api/v1/reports",
                json={
                    "form_id": "intake_startup_ny",
                    "submitter_email": "founder@acme.io",
                    "data": {
                        "organization_name": "Acme Bio",
                        "organization_type": "Startup",
                        "target_state": "NY",
                        "mission_and_pitch": "Biotech diagnostics",
                    },
                },
            )
            self.assertEqual(post_res.status_code, 202)
            body = post_res.json()
            report_id = body["report_id"]
            self.assertEqual(body["status"], "queued")
            self.assertEqual(body["stream_url"], f"/api/v1/reports/{report_id}/stream")

            # Stream SSE events until terminal 'complete' event
            with client.stream(
                "GET",
                f"/api/v1/reports/{report_id}/stream?poll_interval_seconds=0.05",
            ) as stream_res:
                self.assertEqual(stream_res.status_code, 200)
                raw_stream = "".join(stream_res.iter_text())

            self.assertIn("event: stage", raw_stream)
            self.assertIn("query_planning", raw_stream)
            self.assertIn("event: complete", raw_stream)

            # GET /api/v1/reports/{id} returns canonical FundingReport
            get_res = client.get(f"/api/v1/reports/{report_id}")
            self.assertEqual(get_res.status_code, 200)
            self.assertEqual(get_res.json()["status"], "completed")
        finally:
            api_app.report_repository = orig_repo
            api_app.job_dispatcher = orig_dispatcher
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_grounding_blocks_hallucinated_url_with_guessed_source_id_and_prefers_gov(self):
        import asyncio
        from src.models.report import FundingOpportunity
        from src.pipeline.grounding import verify_and_ground_opportunities
        from src.pipeline.registry import RetrievedSourceRegistry, normalize_url

        # Tracking params stripped and query params sorted
        self.assertEqual(
            normalize_url("https://www.esd.ny.gov/grants/?b=2&utm_source=google&a=1#top"),
            normalize_url("https://esd.ny.gov/grants?a=1&b=2&fbclid=xyz"),
        )

        async def _run():
            reg = RetrievedSourceRegistry()
            await reg.register_source(
                url="https://blog.example.com/ny-grants",
                title="Blog about NYSTAR",
                snippet="NYSTAR matching grant",
                score=0.90,
                scope="state",
            )
            await reg.register_source(
                url="https://esd.ny.gov/nystar-matching",
                title="Official NYSTAR Portal",
                snippet="Official ESD NYSTAR program",
                score=0.80,
                scope="state",
            )

            candidates = [
                # Hallucinated URL attempting to hijack SRC-1 via guessed source_id -> MUST drop
                FundingOpportunity(
                    name="Hallucinated Grant",
                    provider="Fake Agency",
                    scope="federal",
                    instrument="Grant",
                    eligibility_fit="high",
                    why_it_fits="Fake",
                    source_url="https://fake-agency.gov/bogus",
                    source_id="SRC-1",
                ),
                # Same program from blog vs official .gov -> dedup should keep .gov
                FundingOpportunity(
                    name="NYSTAR Matching Grant",
                    provider="Empire State Development",
                    scope="state",
                    instrument="Grant",
                    eligibility_fit="high",
                    why_it_fits="Blog version",
                    source_url="https://blog.example.com/ny-grants",
                    source_id="SRC-1",
                ),
                FundingOpportunity(
                    name="NYSTAR Matching Grant",
                    provider="Empire State Development",
                    scope="state",
                    instrument="Grant",
                    eligibility_fit="high",
                    why_it_fits="Official version",
                    source_url="https://esd.ny.gov/nystar-matching",
                    source_id="SRC-2",
                ),
            ]

            res = verify_and_ground_opportunities(candidates, reg)
            self.assertEqual(res.dropped_count, 1)
            self.assertEqual(res.dropped_opportunities[0].name, "Hallucinated Grant")
            self.assertEqual(res.verified_count, 1)
            self.assertEqual(res.verified_opportunities[0].source_url, "https://esd.ny.gov/nystar-matching")

        asyncio.run(_run())

    def test_registry_redirect_extract_enriches_original_record(self):
        import asyncio
        from src.core.tavily import TavilyExtractResponse, TavilyExtractResult
        from src.pipeline.registry import RetrievedSourceRegistry

        async def _run():
            reg = RetrievedSourceRegistry()
            await reg.register_source(
                url="https://esd.ny.gov/grants",
                title="ESD Grants",
                snippet="Short snippet",
                score=0.85,
                scope="state",
            )
            extract_res = TavilyExtractResponse(
                results=[
                    TavilyExtractResult(
                        url="https://esd.ny.gov/grants/overview",
                        raw_content="Full redirected page markdown content.",
                    )
                ]
            )
            updated = await reg.register_extract_response(
                extract_res,
                requested_urls=["https://esd.ny.gov/grants"],
            )
            self.assertEqual(len(reg), 1)
            self.assertEqual(len(updated), 1)
            self.assertEqual(updated[0].source_id, "SRC-1")
            self.assertTrue(updated[0].is_extracted)
            # Both original and redirected URLs verify to SRC-1
            self.assertEqual(reg.verify_url("https://esd.ny.gov/grants/overview").source_id, "SRC-1")

        asyncio.run(_run())

    def test_sqlite_draft_repository_single_thumbnail_and_step_log(self):
        import asyncio
        import os
        import tempfile
        from src.core.draft_repository import SQLiteDraftRepository
        from src.models.draft import ApplicationDraft, BrowserStepEvent

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        async def _run():
            repo = SQLiteDraftRepository(db_path=temp_db)
            draft = ApplicationDraft(
                draft_id="draft-1",
                report_id="rep-1",
                opportunity_name="NYSTAR Matching Grant",
                opportunity_source_url="https://esd.ny.gov/nystar",
                target_portal_url="http://127.0.0.1:3000/demo-portal",
                owner_uid="user-1",
            )
            await repo.create(draft)

            await repo.append_browser_step(
                "draft-1",
                BrowserStepEvent(
                    step_index=1,
                    stage="filling",
                    field_label="Organization Legal Name",
                    action="Filled 'Hudson Grid Bio'",
                    screenshot_b64="FRAME_1_BASE64",
                ),
                status="in_progress",
            )
            await repo.append_browser_step(
                "draft-1",
                BrowserStepEvent(
                    step_index=2,
                    stage="paused_for_review",
                    field_label=None,
                    action="Stopped before final submit",
                    screenshot_b64="FRAME_2_BASE64",
                ),
                status="ready_for_review",
            )

            loaded = await repo.get("draft-1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "ready_for_review")
            self.assertEqual(len(loaded.step_log), 2)
            # step_log must NEVER store screenshot_b64 blobs; latest_screenshot_b64 holds only the newest frame
            self.assertNotIn("screenshot_b64", loaded.step_log[0])
            self.assertEqual(loaded.latest_screenshot_b64, "FRAME_2_BASE64")

            by_rep = await repo.list_by_report("rep-1")
            self.assertEqual(len(by_rep), 1)
            self.assertEqual(by_rep[0].draft_id, "draft-1")

        try:
            asyncio.run(_run())
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_portal_url_ssrf_guard(self):
        from src.drafter.security import validate_portal_url

        # Demo portal origin & public HTTPS URLs allowed
        self.assertEqual(
            validate_portal_url("http://127.0.0.1:3000/demo-portal?program=NYSTAR"),
            "http://127.0.0.1:3000/demo-portal?program=NYSTAR",
        )
        self.assertEqual(
            validate_portal_url("https://grants.example.org/apply"),
            "https://grants.example.org/apply",
        )

        # SSRF / metadata / non-HTTP schemes blocked
        for bad_url in [
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://10.0.0.5:8080/internal",
            "http://127.0.0.1:8000/internal/worker/execute",
        ]:
            with self.assertRaises(ValueError):
                validate_portal_url(bad_url)

    def test_browser_draft_orchestrator_end_to_end_and_auth_halt(self):
        import asyncio
        from src.drafter.browser import SimulatedBrowserDriver
        from src.drafter.mapper import FieldFillAction, FormFillPlan
        from src.drafter.orchestrator import BrowserDraftOrchestrator
        from src.models.report import FundingReport

        class FakeMapper:
            async def plan_fill(
                self,
                *,
                form_schema,
                fact_ledger,
                opportunity_name,
                opportunity_source_url,
                opportunity_context=None,
            ):
                actions = []
                for field in form_schema.fields:
                    if field.selector in ("#federal_ein", "#sam_uei"):
                        supplied = fact_ledger.get("user_supplied_facts", {}).get(field.selector)
                        if supplied:
                            actions.append(
                                FieldFillAction(
                                    selector=field.selector,
                                    field_label=field.label,
                                    action_type="fill",
                                    value=supplied,
                                )
                            )
                        else:
                            actions.append(
                                FieldFillAction(
                                    selector=field.selector,
                                    field_label=field.label,
                                    action_type="skip_missing",
                                    missing_question=f"Enter {field.label}",
                                )
                            )
                    elif field.input_type == "select":
                        actions.append(
                            FieldFillAction(
                                selector=field.selector,
                                field_label=field.label,
                                action_type="select",
                                value=field.options[0],
                            )
                        )
                    elif field.input_type == "checkbox":
                        actions.append(
                            FieldFillAction(
                                selector=field.selector,
                                field_label=field.label,
                                action_type="check",
                                value="true",
                            )
                        )
                    else:
                        actions.append(
                            FieldFillAction(
                                selector=field.selector,
                                field_label=field.label,
                                action_type="fill",
                                value=f"Drafted {field.label}",
                            )
                        )
                return FormFillPlan(
                    actions=actions,
                    narrative_summary_markdown=f"## {form_schema.heading}\nDrafted content.",
                )

        report = FundingReport(
            report_id="rep-draft-test",
            status="completed",
            org_name="Hudson Grid Bio",
            org_type="Startup",
            target_state="NY",
            submitter_email="founder@hudson.io",
            intake_data={"organization_name": "Hudson Grid Bio"},
        )

        async def _run():
            events = []

            async def on_step(evt, status):
                events.append((evt.stage, status))

            orch = BrowserDraftOrchestrator(
                browser_driver=SimulatedBrowserDriver(),
                mapper=FakeMapper(),
            )
            draft = await orch.run(
                report=report,
                opportunity_name="NYSTAR Matching Grant",
                opportunity_source_url="https://esd.ny.gov/nystar",
                target_portal_url="http://127.0.0.1:3000/demo-portal",
                on_step=on_step,
            )
            self.assertEqual(draft.status, "ready_for_review")
            self.assertEqual(len(draft.missing_inputs), 2)
            self.assertEqual(draft.metadata["fields_skipped_missing"], 2)
            self.assertGreaterEqual(draft.metadata["fields_filled"], 10)
            self.assertIsNotNone(draft.latest_screenshot_b64)
            self.assertEqual(events[-1][0], "paused_for_review")

            # Auth/login wall must halt immediately with awaiting_human_action
            blocked = await orch.run(
                report=report,
                opportunity_name="Gated Portal Grant",
                opportunity_source_url="https://example.gov/apply",
                target_portal_url="https://example.gov/login",
            )
            self.assertEqual(blocked.status, "awaiting_human_action")
            self.assertIn("login", (blocked.metadata.get("blocked_reason") or "").lower())

        asyncio.run(_run())

    def test_draft_api_create_stream_revise_and_export(self):
        import asyncio
        import os
        import tempfile
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        import src.api.app as api_app
        from src.core.draft_repository import SQLiteDraftRepository
        from src.core.repository import SQLiteReportRepository
        from src.drafter.browser import SimulatedBrowserDriver
        from src.drafter.mapper import FieldFillAction, FormFillPlan
        from src.drafter.orchestrator import BrowserDraftOrchestrator
        from src.models.report import FundingOpportunity, FundingReport

        class FakeMapper:
            async def plan_fill(self, *, form_schema, fact_ledger, **kwargs):
                actions = []
                for field in form_schema.fields:
                    if field.selector == "#federal_ein":
                        ein = fact_ledger.get("user_supplied_facts", {}).get("federal_ein")
                        if ein:
                            actions.append(FieldFillAction(selector=field.selector, field_label=field.label, action_type="fill", value=ein))
                        else:
                            actions.append(FieldFillAction(selector=field.selector, field_label=field.label, action_type="skip_missing", missing_question="Provide Federal EIN"))
                    elif field.input_type == "select":
                        actions.append(FieldFillAction(selector=field.selector, field_label=field.label, action_type="select", value=field.options[0]))
                    elif field.input_type == "checkbox":
                        actions.append(FieldFillAction(selector=field.selector, field_label=field.label, action_type="check", value="true"))
                    else:
                        actions.append(FieldFillAction(selector=field.selector, field_label=field.label, action_type="fill", value="Test Value"))
                return FormFillPlan(actions=actions, narrative_summary_markdown="## Draft Narrative\nGrounded copy.")

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        orig_report_repo = api_app.report_repository
        orig_draft_repo = api_app.draft_repository

        try:
            rep_repo = SQLiteReportRepository(db_path=temp_db)
            drf_repo = SQLiteDraftRepository(db_path=temp_db)
            api_app.report_repository = rep_repo
            api_app.draft_repository = drf_repo

            report = FundingReport(
                report_id="rep-api-draft",
                status="completed",
                org_name="Hudson Grid Bio",
                org_type="Startup",
                target_state="NY",
                submitter_email="founder@hudson.io",
                intake_data={"organization_name": "Hudson Grid Bio"},
                opportunities=[
                    FundingOpportunity(
                        name="NYSTAR Matching Grant",
                        provider="ESD",
                        scope="state",
                        instrument="Grant",
                        eligibility_fit="high",
                        why_it_fits="NY startup",
                        source_url="https://esd.ny.gov/nystar",
                        source_id="SRC-1",
                        citation_verified=True,
                    )
                ],
            )
            asyncio.run(rep_repo.create(report))

            def _fake_orchestrator(*args, **kwargs):
                return BrowserDraftOrchestrator(
                    browser_driver=SimulatedBrowserDriver(),
                    mapper=FakeMapper(),
                )

            with patch.object(api_app, "BrowserDraftOrchestrator", side_effect=_fake_orchestrator):
                client = TestClient(api_app.app)

                # SSRF URL rejected with 400
                bad_res = client.post(
                    "/api/v1/reports/rep-api-draft/drafts",
                    json={"opportunity_index": 0, "target_portal_url": "http://169.254.169.254/latest/meta-data/"},
                )
                self.assertEqual(bad_res.status_code, 400)

                # Valid draft creation -> 202 Accepted
                post_res = client.post(
                    "/api/v1/reports/rep-api-draft/drafts",
                    json={"opportunity_index": 0},
                )
                self.assertEqual(post_res.status_code, 202)
                draft_id = post_res.json()["draft_id"]

                # SSE stream emits browser_step + complete
                with client.stream("GET", f"/api/v1/drafts/{draft_id}/stream?poll_interval_seconds=0.05") as s:
                    self.assertEqual(s.status_code, 200)
                    raw_sse = "".join(s.iter_text())
                self.assertIn("event: browser_step", raw_sse)
                self.assertIn("screenshot_b64", raw_sse)
                self.assertIn("event: complete", raw_sse)

                # GET draft has missing_inputs for EIN
                get_res = client.get(f"/api/v1/drafts/{draft_id}")
                self.assertEqual(get_res.status_code, 200)
                self.assertEqual(get_res.json()["status"], "ready_for_review")
                self.assertEqual(len(get_res.json()["missing_inputs"]), 1)

                # Revise draft with EIN -> version 2 has 0 missing_inputs
                rev_res = client.post(
                    f"/api/v1/drafts/{draft_id}/revise",
                    json={"user_supplied_facts": {"federal_ein": "12-3456789"}},
                )
                self.assertEqual(rev_res.status_code, 202)
                with client.stream("GET", f"/api/v1/drafts/{draft_id}/stream?poll_interval_seconds=0.05") as s2:
                    "".join(s2.iter_text())
                revised = client.get(f"/api/v1/drafts/{draft_id}").json()
                self.assertEqual(revised["version"], 2)
                self.assertEqual(len(revised["missing_inputs"]), 0)

                # Export Markdown
                exp_res = client.get(f"/api/v1/drafts/{draft_id}/export")
                self.assertEqual(exp_res.status_code, 200)
                self.assertIn("# Application Draft — NYSTAR Matching Grant", exp_res.text)
                self.assertIn("12-3456789", exp_res.text)
        finally:
            api_app.report_repository = orig_report_repo
            api_app.draft_repository = orig_draft_repo
            if os.path.exists(temp_db):
                os.remove(temp_db)

    def test_report_storage_lifecycle(self):
        import tempfile
        import os
        from src.core.storage import ReportStorage

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            store = ReportStorage(db_path=temp_db)
            report = store.create_report(
                report_id="test-rep-1",
                org_name="CleanEnergy Labs",
                org_type="Startup",
                target_state="NJ",
                funding_type="Equity Investment",
                funding_amount="1,000,000",
                status="in_progress",
            )
            self.assertEqual(report.status, "in_progress")
            self.assertEqual(report.org_name, "CleanEnergy Labs")
            self.assertEqual(report.target_state, "NJ")

            # Update report to completed
            updated = store.update_report(
                report_id="test-rep-1",
                status="completed",
                summary="Strong readiness.",
                subagent_results=[{"subagent_type": "market", "output": "High market demand."}],
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated.status, "completed")
            self.assertEqual(len(updated.subagent_results), 1)

            # Retrieve report
            retrieved = store.get_report("test-rep-1")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.summary, "Strong readiness.")

            # List reports
            all_reports = store.list_reports()
            self.assertGreaterEqual(len(all_reports), 1)
            self.assertEqual(all_reports[0].id, "test-rep-1")
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)


if __name__ == "__main__":
    unittest.main()
