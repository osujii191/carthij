from typing import Any, Callable, Dict, List, Optional
from src.core.agent import BaseAgent


def create_subagent(
    name: str,
    role: str,
    system_instruction: str,
    model: str = "gemini-2.5-flash",
    tools: Optional[List[Callable[..., Any]]] = None,
) -> BaseAgent:
    """Convenience helper to create a specialized subagent."""
    return BaseAgent(
        name=name,
        role=role,
        system_instruction=system_instruction,
        model=model,
        tools=tools,
    )
