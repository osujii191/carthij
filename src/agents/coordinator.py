import asyncio
import logging
import uuid
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from src.core.agent import BaseAgent
from src.agents.subagent import create_subagent
from src.agents.planner import QueryPlanner
from src.api.schemas import (
    FormSubmissionRequest,
    SubagentResult,
    SubagentTask,
    WorkflowExecutionResponse,
)

logger = logging.getLogger(__name__)


class TaskDecompositionPlan(BaseModel):
    """Structured plan produced by the coordinator."""
    plan_overview: str = Field(description="Summary of the execution plan")
    tasks: List[SubagentTask] = Field(description="List of tasks to fan out to subagents")


class CoordinatorAgent:
    """
    Supervisor / Coordinator Agent.
    1. Analyzes the submitted form data.
    2. Decomposes work into parallel subagent tasks.
    3. Executes subagents in parallel with asyncio.gather().
    4. Synthesizes findings into a final workflow response.
    """

    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model
        self.planner = BaseAgent(
            name="coordinator_planner",
            role="Task Planner & Orchestrator",
            system_instruction=(
                "You are an expert orchestrator for an AI funding readiness workflow. "
                "You receive structured intake data from Startups and Non-Profits, including their target jurisdiction (NY, NJ, or SF/CA). "
                "Analyze the data and decompose it into 2 to 4 distinct, parallel tasks for specialized subagents. "
                "Ensure you include specialized jurisdictional analysis: differentiate State/Local programs "
                "(such as Empire State Development/NYSTAR in NY, NJEDA/NJCSIT incentives in NJ, or California/SF municipal grants and tax credits) "
                "from Federal programs (such as SBIR/STTR, NSF, NIH, or federal foundation grants)."
            ),
            model=self.model,
            response_schema=TaskDecompositionPlan,
        )

        self.synthesizer = BaseAgent(
            name="coordinator_synthesizer",
            role="Result Synthesizer",
            system_instruction=(
                "You are an expert executive synthesizer. You receive the original user form submission "
                "along with findings from multiple parallel subagents. "
                "Synthesize their findings into a cohesive, structured executive summary with clear next steps."
            ),
            model=self.model,
        )

    async def execute_subagent_task(self, task: SubagentTask) -> SubagentResult:
        """Executes an individual subagent task safely."""
        logger.info(f"Starting subagent '{task.subagent_type}'...")
        try:
            subagent = create_subagent(
                name=f"worker_{task.subagent_type}",
                role=task.subagent_type,
                system_instruction=(
                    f"You are a specialized {task.subagent_type} agent. "
                    "Analyze the given information thoroughly and provide specific, high-quality analysis."
                ),
                model=self.model,
            )
            prompt = f"Task: {task.prompt}\nContext: {task.context}"
            output = await subagent.run(prompt)
            return SubagentResult(
                subagent_type=task.subagent_type,
                status="completed",
                output=output,
            )
        except Exception as exc:
            logger.exception(f"Subagent '{task.subagent_type}' failed: {exc}")
            return SubagentResult(
                subagent_type=task.subagent_type,
                status="failed",
                output="",
                error=str(exc),
            )

    async def run(self, form: FormSubmissionRequest) -> WorkflowExecutionResponse:
        """Runs the complete coordinator workflow end-to-end with query planning & live research."""
        workflow_id = str(uuid.uuid4())
        logger.info(f"Starting workflow {workflow_id} for form: {form.form_id or 'default'}")

        # Step 1: Query Understanding & Parallel Tavily Web Research
        query_planner = QueryPlanner(model=self.model)
        query_plan = await query_planner.generate_plan(form.data, form.context or "")
        search_responses = await query_planner.execute_plan(query_plan)
        research_context = query_planner.format_search_results_for_context(query_plan, search_responses)

        # Step 2: Decompose form into parallel subagent tasks enriched with research
        plan_prompt = (
            f"Form Data:\n{form.data}\n\n"
            f"Live Verified Web Research (Tavily):\n{research_context}\n\n"
            f"Additional Context:\n{form.context or 'None provided'}"
        )
        plan_json = await self.planner.run(plan_prompt)
        plan = TaskDecompositionPlan.model_validate_json(plan_json)

        logger.info(f"Plan generated with {len(plan.tasks)} parallel subagent tasks.")

        # Step 3: Fan out subagent tasks in parallel with research context
        for task in plan.tasks:
            task.context["research_findings"] = research_context

        subagent_results = await asyncio.gather(
            *(self.execute_subagent_task(task) for task in plan.tasks)
        )

        # Step 4: Synthesize subagent findings and research context
        findings_str = "\n\n".join(
            f"[{res.subagent_type} ({res.status})]:\n{res.output or res.error}"
            for res in subagent_results
        )
        synthesis_prompt = (
            f"Original Form Data:\n{form.data}\n\n"
            f"Live Research Context (Tavily):\n{research_context}\n\n"
            f"Subagent Deep-Dive Findings:\n{findings_str}\n\n"
            "Please provide a final synthesized readiness report. "
            "Clearly distinguish between State/Local programs (NY, NJ, or SF) and Federal programs, "
            "highlight eligibility criteria, deadlines, and provide prioritized next steps."
        )
        final_summary = await self.synthesizer.run(synthesis_prompt)

        return WorkflowExecutionResponse(
            workflow_id=workflow_id,
            status="completed",
            summary=final_summary,
            subagent_results=subagent_results,
            metadata={
                "form_id": form.form_id,
                "submitter_email": form.submitter_email,
                "total_subagents": len(subagent_results),
                "planned_queries": [pq.query for pq in query_plan.queries],
                "research_strategy": query_plan.strategy,
            },
        )
