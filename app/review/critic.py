from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.config import Settings
from app.exceptions import LLMError
from app.github.diff_parser import ParsedFile
from app.models import CriticResult, Finding

logger = logging.getLogger(__name__)

CRITIC_SYSTEM_PROMPT = """You are a review critic integrated into a GitHub App.
Your job is to evaluate candidate code-review findings and decide what to keep, revise,
merge, or remove.

Rules:
1. Evaluate each candidate finding for:
   — duplicates or semantically equivalent findings
   — claims not supported by the diff
   — incorrect severity
   — vague descriptions
   — weak or impractical suggestions
   — comments that do not correspond to changed code
   — findings that should be merged into one
   — important obvious issues missing from the candidate set
2. For each finding, output one decision: keep, revise, merge, or remove.
3. When merging, set merge_into_index to the index of the finding to keep.
4. Set strong_reason=True only when you are highly confident the action is correct.
5. Deterministic static findings (listed first) should be preserved unless they
   are clearly wrong — if you do recommend removing or revising one, set
   strong_reason=True and explain thoroughly.
6. Never suggest a file path or line number that is not present in the diff.
7. If the candidate set is missing an obvious issue, add it in missing_findings.
8. Output must match the provided JSON schema exactly."""


CRITIC_USER_PROMPT_TEMPLATE = """Pull request diff:

```diff
{diff_text}
```

Candidate findings:

{candidate_findings}

For each finding, decide: keep, revise, merge, or remove.  If you see an
obvious issue that is missing from the candidate set, add it in
missing_findings."""


def _format_candidate(i: int, f: Finding) -> str:
    line = f" (line {f.line})" if f.line else ""
    return (
        f"[{i}] [{f.severity.value}] [{f.category.value}] {f.title}"
        f" in {f.file_path}{line}\n"
        f"    confidence={f.confidence}  explanation={f.explanation}\n"
        f"    failure_scenario={f.failure_scenario}\n"
        f"    suggestion={f.suggestion}"
    )


class CriticEngine:
    """LLM-based critic that reviews candidate findings."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.critic_timeout,
        )

    async def criticize(
        self,
        *,
        diff_text: str,
        candidate_findings: list[Finding],
        parsed_files: list[ParsedFile],
    ) -> CriticResult | None:
        """Run the critic pass.  Returns None on failure (caller should fall back)."""
        lines = []
        for i, f in enumerate(candidate_findings):
            lines.append(_format_candidate(i, f))
        candidate_text = "\n".join(lines) if lines else "(none)"

        user_prompt = CRITIC_USER_PROMPT_TEMPLATE.format(
            diff_text=diff_text[: self.settings.max_chars],
            candidate_findings=candidate_text,
        )

        schema = CriticResult.model_json_schema()
        from app.review.engine import _make_strict_schema

        schema = _make_strict_schema(schema)

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "critic_result",
                        "schema": schema,
                        "strict": True,
                    },
                },
                temperature=0.1,
                max_tokens=4000,
            )
        except Exception as exc:
            logger.warning("Critic LLM request failed: %s", type(exc).__name__)
            return None

        content = response.choices[0].message.content
        if not content:
            logger.warning("Critic returned empty content")
            return None

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Critic returned invalid JSON: %s", exc)
            return None

        try:
            return CriticResult.model_validate(data)
        except Exception as exc:
            logger.warning("Critic response failed schema validation: %s", exc)
            return None

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
