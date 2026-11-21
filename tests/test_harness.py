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

        client = TavilyClient(api_key=None)
        res = asyncio.run(client.search("NJEDA angel tax credit"))
        self.assertEqual(res.query, "NJEDA angel tax credit")
        self.assertGreaterEqual(len(res.results), 1)
        self.assertIn("Simulated search results", res.results[0].content)

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
