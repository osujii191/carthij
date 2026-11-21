import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, Type
from google import genai
from google.genai import types
from pydantic import BaseModel

from src.core.client import DEFAULT_GEMINI_MODEL, get_genai_client

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Lightweight, modular agent harness built directly on Google GenAI SDK.
    Supports:
    - System instructions
    - Native tool / function calling loop
    - Structured JSON outputs via Pydantic
    - Isolated message history per agent instance
    """

    def __init__(
        self,
        name: str,
        role: str,
        system_instruction: str,
        model: str = DEFAULT_GEMINI_MODEL,
        tools: Optional[List[Callable[..., Any]]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        client: Optional[genai.Client] = None,
        max_tool_iterations: int = 8,
    ):
        self.name = name
        self.role = role
        self.system_instruction = system_instruction
        self.model = model
        self._client = client
        self.max_tool_iterations = max_tool_iterations
        self.response_schema = response_schema

        self._tools_map: Dict[str, Callable[..., Any]] = {}
        self._tools_list: List[Callable[..., Any]] = []
        if tools:
            for tool in tools:
                self.register_tool(tool)

        self.history: List[types.Content] = []

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = get_genai_client()
        return self._client

    def register_tool(self, func: Callable[..., Any]) -> None:
        """Register a Python callable as an agent tool."""
        self._tools_map[func.__name__] = func
        self._tools_list.append(func)

    def _build_config(
        self,
        *,
        include_tools: bool = True,
        include_schema: bool = True,
    ) -> types.GenerateContentConfig:
        config_kwargs: Dict[str, Any] = {
            "system_instruction": self.system_instruction,
        }
        if include_tools and self._tools_list:
            config_kwargs["tools"] = self._tools_list
        if include_schema and self.response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = self.response_schema

        return types.GenerateContentConfig(**config_kwargs)

    async def run(self, prompt: str, *, reset_history: bool = True) -> str:
        """
        Executes an agent turn with native async function calling loop.
        Resets `self.history` by default on each call so reused agent instances never leak
        prior request context across runs.
        When both `tools` and `response_schema` are configured, runs two-phase execution:
        1. Tool-calling turns with `tools` enabled and `response_schema` disabled (ensuring
           consistent behavior across Gemini Developer API and Vertex AI).
        2. Final structured turn with `response_schema` enabled and `tools` disabled.
        """
        if reset_history:
            self.history = []

        # Append user message to history
        self.history.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        )

        two_phase = bool(self._tools_list and self.response_schema)
        config = self._build_config(
            include_tools=True,
            include_schema=not two_phase,
        )

        response = None
        for iteration in range(self.max_tool_iterations):
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=self.history,
                config=config,
            )

            # Record model candidate in history safely
            candidates = getattr(response, "candidates", None) or []
            if candidates and getattr(candidates[0], "content", None) is not None:
                self.history.append(candidates[0].content)

            # Check if model requested function calls
            function_calls = getattr(response, "function_calls", None)
            if not function_calls:
                if not two_phase:
                    return response.text or ""
                break

            # Execute function calls
            tool_response_parts: List[types.Part] = []
            for call in function_calls:
                fn_name = call.name
                fn_args = call.args or {}
                logger.info(f"[{self.name}] Calling tool '{fn_name}' with args: {fn_args}")

                tool_fn = self._tools_map.get(fn_name)
                if not tool_fn:
                    result = {"error": f"Tool '{fn_name}' not found."}
                else:
                    try:
                        if inspect.iscoroutinefunction(tool_fn):
                            result = await tool_fn(**fn_args)
                        else:
                            result = tool_fn(**fn_args)
                    except Exception as exc:
                        logger.exception(f"[{self.name}] Error executing tool '{fn_name}': {exc}")
                        result = {"error": str(exc)}

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": result},
                    )
                )

            # Append tool outputs back to conversation
            self.history.append(
                types.Content(
                    role="tool",
                    parts=tool_response_parts,
                )
            )
        else:
            logger.warning(f"[{self.name}] Exceeded max tool iterations ({self.max_tool_iterations}).")

        if two_phase:
            self.history.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "Now produce your final output strictly matching the required JSON schema "
                                "based on the provided context and any tool results above."
                            )
                        )
                    ],
                )
            )
            structured_config = self._build_config(include_tools=False, include_schema=True)
            final_response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=self.history,
                config=structured_config,
            )
            final_candidates = getattr(final_response, "candidates", None) or []
            if final_candidates and getattr(final_candidates[0], "content", None) is not None:
                self.history.append(final_candidates[0].content)
            return final_response.text or ""

        return (response.text if response else None) or "Max iterations reached without final response."
