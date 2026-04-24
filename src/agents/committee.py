"""Investment Committee orchestrator.

Coordinates three Claude agents in sequence:
  1. Bull  — builds the strongest case FOR the basin
  2. Bear  — builds the strongest case AGAINST
  3. PM    — reads both and issues a binding verdict

Usage::

    import anthropic
    from agents.committee import Committee

    client = anthropic.Anthropic(api_key="...")
    result = Committee(client).debate("Permian", "oil", 2028)
    print(result["pm"]["text_response"])
    print(Committee.parse_pm_verdict(result["pm"]["text_response"]))
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from agents.prompts import BEAR_SYSTEM_PROMPT, BULL_SYSTEM_PROMPT, PM_SYSTEM_PROMPT
from agents.tools import TOOL_SPECS, execute_tool

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE = 2  # seconds


class Committee:
    """Run a Bull / Bear / PM investment debate for a named basin.

    Parameters
    ----------
    anthropic_client:
        An initialised ``anthropic.Anthropic`` instance.
    model:
        Claude model ID used for all three agents.
    """

    def __init__(
        self,
        anthropic_client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-5-20250929",
    ) -> None:
        self._client = anthropic_client
        self.model = model

    # ------------------------------------------------------------------
    # API call with retry
    # ------------------------------------------------------------------

    def _create(
        self,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Call messages.create with exponential-backoff on rate limits / 5xx."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 800,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES:
                    break
                wait = _BACKOFF_BASE ** attempt
                logger.warning("Rate limited — retrying in %ds (attempt %d/%d)", wait, attempt, _MAX_RETRIES)
                time.sleep(wait)
            except anthropic.APIStatusError as exc:
                last_exc = exc
                if attempt == _MAX_RETRIES or exc.status_code < 500:
                    break
                wait = _BACKOFF_BASE ** attempt
                logger.warning("API %d error — retrying in %ds", exc.status_code, wait)
                time.sleep(wait)
        raise last_exc

    # ------------------------------------------------------------------
    # Tool-use loop
    # ------------------------------------------------------------------

    def run_agent(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        max_turns: int = 8,
    ) -> dict[str, Any]:
        """Run a single agent with a full tool-use agentic loop.

        Sends the initial message, executes any tool calls, feeds results back,
        and repeats until the model produces a text-only response or *max_turns*
        is exhausted.

        Returns a dict with keys:
          text_response  — the agent's final prose output
          tool_calls     — list of {tool, input, result} dicts
          messages_log   — full conversation history
        """
        # System content with cache_control so the prompt is cached for 5 min
        system_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        tool_calls_log: list[dict[str, Any]] = []

        for turn in range(max_turns):
            response = self._create(
                system=system_content,
                messages=messages,
                tools=tools if tools else None,
            )

            # Append this assistant turn to the conversation
            messages.append({"role": "assistant", "content": response.content})

            has_tool_use = any(
                getattr(block, "type", None) == "tool_use" for block in response.content
            )

            # Done when no more tool calls are requested
            if not has_tool_use:
                text = " ".join(
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ).strip()
                if response.stop_reason == "max_tokens":
                    logger.warning("Agent hit max_tokens on turn %d — response may be truncated", turn)
                return {
                    "text_response": text,
                    "tool_calls": tool_calls_log,
                    "messages_log": messages,
                }

            # Execute each tool call and collect results
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
                logger.debug("Tool '%s' → %d-key result", block.name, len(result))

            messages.append({"role": "user", "content": tool_results})

        # max_turns hit — extract whatever text is available
        logger.warning("max_turns=%d exhausted before end_turn", max_turns)
        text = ""
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                content = msg["content"]
                blocks = content if isinstance(content, list) else []
                for b in blocks:
                    if hasattr(b, "text"):
                        text = b.text
                        break
            if text:
                break

        return {
            "text_response": text,
            "tool_calls": tool_calls_log,
            "messages_log": messages,
        }

    # ------------------------------------------------------------------
    # Debate orchestration
    # ------------------------------------------------------------------

    def debate(
        self,
        basin: str,
        fuel_type: str,
        target_year: int,
        wti_assumption: float = 75.0,
    ) -> dict[str, Any]:
        """Run the full Bull → Bear → PM investment committee debate.

        Parameters
        ----------
        basin:
            One of the 7 supported basin names (e.g. "Permian").
        fuel_type:
            "oil" or "gas".
        target_year:
            The investment horizon year the agents should evaluate.
        wti_assumption:
            WTI price assumption in $/bbl passed to revenue KPIs.

        Returns
        -------
        dict with keys: bull, bear, pm, metadata.
        Each of bull/bear/pm is the dict returned by run_agent.
        """
        t0 = time.time()

        context = (
            f"Basin: **{basin}**  |  Fuel: {fuel_type}  |  "
            f"Target year: {target_year}  |  WTI assumption: ${wti_assumption}/bbl\n\n"
            "Use your available tools to pull live data before writing your thesis. "
            "Do not speculate on numbers you have not retrieved from a tool."
        )

        logger.info(
            "Committee debate — basin=%s fuel=%s year=%d wti=%.0f",
            basin, fuel_type, target_year, wti_assumption,
        )

        bull = self.run_agent(BULL_SYSTEM_PROMPT, context, TOOL_SPECS)
        logger.info("Bull complete — %d tool calls", len(bull["tool_calls"]))

        bear = self.run_agent(BEAR_SYSTEM_PROMPT, context, TOOL_SPECS)
        logger.info("Bear complete — %d tool calls", len(bear["tool_calls"]))

        pm_context = (
            f"Investment committee debate — **{basin}** {fuel_type} "
            f"(target year: {target_year}, WTI: ${wti_assumption}/bbl).\n\n"
            f"{'═' * 50}\n"
            f"BULL ANALYST — Riley Chen\n"
            f"{'═' * 50}\n"
            f"{bull['text_response']}\n\n"
            f"{'═' * 50}\n"
            f"BEAR ANALYST — Marcus Webb\n"
            f"{'═' * 50}\n"
            f"{bear['text_response']}\n\n"
            "Issue your binding investment committee verdict."
        )
        pm = self.run_agent(PM_SYSTEM_PROMPT, pm_context, [])
        logger.info("PM complete — verdict issued")

        total_tool_calls = (
            len(bull["tool_calls"]) + len(bear["tool_calls"]) + len(pm["tool_calls"])
        )
        elapsed = round(time.time() - t0, 1)

        return {
            "bull": bull,
            "bear": bear,
            "pm": pm,
            "metadata": {
                "basin": basin,
                "fuel_type": fuel_type,
                "target_year": target_year,
                "wti_assumption": wti_assumption,
                "total_tool_calls": total_tool_calls,
                "latency_seconds": elapsed,
                "model": self.model,
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_pm_verdict(pm_text: str) -> dict[str, str]:
        """Extract structured fields from the PM's formatted output.

        Returns a dict with keys: verdict, conviction, rationale,
        top_risk, top_opportunity. Values are empty strings if the
        field was not found (e.g. model deviated from format).
        """
        fields = ["VERDICT", "CONVICTION", "RATIONALE", "TOP_RISK", "TOP_OPPORTUNITY"]
        result: dict[str, str] = {}

        for i, field in enumerate(fields):
            marker = f"{field}:"
            start = pm_text.find(marker)
            if start == -1:
                result[field.lower()] = ""
                continue
            value_start = start + len(marker)
            # Find where the next field begins
            next_pos = len(pm_text)
            for successor in fields[i + 1:]:
                pos = pm_text.find(f"{successor}:", value_start)
                if pos != -1 and pos < next_pos:
                    next_pos = pos
            result[field.lower()] = pm_text[value_start:next_pos].strip()

        return result
