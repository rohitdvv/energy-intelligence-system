"""Conversational AI energy analyst agent.

A single-turn tool-use agent that answers questions grounded in live EIA data.
Each call to respond() starts a fresh agentic loop from the full conversation
history, calling tools as needed before producing a text response.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from agents.tools import TOOL_SPECS, execute_tool

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """\
You are an AI energy analyst with direct access to live U.S. oil and gas production \
data from the EIA Open Data API.

You help investment professionals answer questions about U.S. basin production, \
forecasts, KPIs, and market dynamics.

DATA GROUNDING RULES (mandatory):
1. For any specific production figure, percentage, or volume: call the appropriate \
   tool FIRST. Never state a number from memory.
2. Prefix every data-backed statement with [DATA] and cite the source tool.
3. Prefix any inference or interpretation not directly from a tool with [INFERENCE].
4. If a question asks you to model a scenario (e.g. "what if decline rate is 15% \
   steeper"), use the best tool-backed data as your base case, clearly state your \
   adjustment, and label the result [INFERENCE].

AVAILABLE TOOLS:
- get_production_history  : latest monthly stats for a basin (trend, YoY, averages)
- forecast_basin          : Prophet forecast through a target year + 80% CI
- get_kpi_snapshot        : full KPI suite (growth, decline, volatility, revenue)
- compare_basins          : rank all 7 basins by projected production with RPI scores
- investigate_anomalies   : detect anomalous production months with event context

RESPONSE STYLE:
- Lead with a direct answer or data retrieval — do not over-explain
- Use bullet points for multi-part responses
- For comparisons, use a sorted list with the key metric shown
- Keep responses concise and decision-relevant (under 300 words)

BASINS AVAILABLE: Permian, Bakken, Eagle Ford, Marcellus, Haynesville, Anadarko, Appalachian
"""


class ChatAgent:
    """Single-turn conversational analyst with a full tool-use loop.

    Parameters
    ----------
    client:
        An initialised ``anthropic.Anthropic`` instance.
    model:
        Claude model ID to use for all responses.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-5-20250929",
    ) -> None:
        self._client = client
        self.model = model

    def respond(
        self,
        messages: list[dict[str, Any]],
        max_turns: int = 8,
    ) -> dict[str, Any]:
        """Run the tool-use loop and return a final text response.

        Parameters
        ----------
        messages:
            Full conversation history as plain ``[{"role": ..., "content": str}]``
            dicts. Tool call blocks from prior turns are NOT required — the agent
            will re-fetch any data it needs for the current question.
        max_turns:
            Maximum tool-call rounds before forcing a text response.

        Returns
        -------
        dict with keys:
          text       — the agent's final text response (markdown)
          tool_calls — list of {tool, input, result} dicts executed this turn
        """
        system: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": CHAT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        conversation = list(messages)
        tool_calls_log: list[dict[str, Any]] = []

        for _ in range(max_turns):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=system,
                messages=conversation,
                tools=TOOL_SPECS,
            )
            conversation.append({"role": "assistant", "content": response.content})

            has_tool_use = any(
                getattr(b, "type", None) == "tool_use" for b in response.content
            )

            if not has_tool_use:
                text = " ".join(
                    b.text for b in response.content if hasattr(b, "text")
                ).strip()
                return {"text": text, "tool_calls": tool_calls_log}

            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                result = execute_tool(block.name, block.input)
                tool_calls_log.append(
                    {"tool": block.name, "input": block.input, "result": result}
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )
                logger.debug("Chat tool '%s' → %d-key result", block.name, len(result))

            conversation.append({"role": "user", "content": tool_results})

        logger.warning("ChatAgent: max_turns=%d exhausted", max_turns)
        return {
            "text": (
                "I was unable to complete the analysis within the allowed tool-call "
                "budget. Please try a more specific question."
            ),
            "tool_calls": tool_calls_log,
        }
