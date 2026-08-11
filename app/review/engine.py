from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.exceptions import LLMError
from app.models import Finding, ReviewResult
from app.review.prompts import (
    REVIEW_SYSTEM_PROMPT,
    REVIEW_USER_PROMPT_TEMPLATE,
    FEW_SHOT_EXAMPLES,
    build_language_context,
    format_static_findings,
)

logger = logging.getLogger(__name__)


def _make_strict_schema(schema: dict) -> dict:
    """Convert a JSON schema to be compatible with OpenAI strict mode.

    OpenAI strict requires every property to be in ``required`` and uses
    a flat ``type`` list instead of ``anyOf`` for nullable fields.
    It also does not support ``$ref`` — all references must be inlined.
    """
    defs = schema.get("$defs", {})

    def _resolve_ref(ref: str) -> dict:
        path = ref.lstrip("#/").split("/")
        node = schema
        for part in path:
            node = node.get(part, {})
        return dict(node)

    def _fix(s: dict) -> None:
        props = s.get("properties")
        if props:
            s["required"] = list(props.keys())
            for key, prop in props.items():
                if "$ref" in prop:
                    resolved = _resolve_ref(prop["$ref"])
                    prop.clear()
                    prop.update(resolved)

                if "anyOf" in prop:
                    resolved_anyOf = []
                    for opt in prop["anyOf"]:
                        if "$ref" in opt:
                            opt = _resolve_ref(opt["$ref"])
                        resolved_anyOf.append(opt)

                    types: list[str] = []
                    has_null = False
                    for opt in resolved_anyOf:
                        t = opt.get("type")
                        if t == "null":
                            has_null = True
                        elif t:
                            types.append(t)
                        if not t and "enum" in opt:
                            first = opt["enum"][0]
                            if isinstance(first, str):
                                t = "string"
                            elif isinstance(first, bool):
                                t = "boolean"
                            elif isinstance(first, int):
                                t = "integer"
                            elif isinstance(first, float):
                                t = "number"
                            if t and t != "null":
                                types.append(t)

                    if types:
                        prop["type"] = types if len(types) > 1 else types[0]
                    if has_null:
                        prop_type = prop.get("type")
                        if isinstance(prop_type, list):
                            if "null" not in prop_type:
                                prop_type.append("null")
                        elif prop_type:
                            prop["type"] = [prop_type, "null"]
                        else:
                            prop["type"] = ["null"]
                    prop.pop("anyOf", None)
                    prop.pop("default", None)

                if prop.get("type") == "object" and "properties" in prop:
                    _fix(prop)
                if "items" in prop and isinstance(prop["items"], dict):
                    if "$ref" in prop["items"]:
                        resolved = _resolve_ref(prop["items"]["$ref"])
                        prop["items"] = resolved
                    if "properties" in prop["items"]:
                        _fix(prop["items"])

    _fix(schema)
    for defn in defs.values():
        _fix(defn)
    schema.pop("$defs", None)
    return schema


def _pick_temperature(diff_chars: int, model: str) -> float:
    """Choose a temperature based on diff size and model.

    Larger diffs get slightly higher temperature to encourage broader
    coverage. Smaller, focused diffs stay colder for precision.
    """
    if diff_chars < 10_000:
        return 0.15
    elif diff_chars < 50_000:
        return 0.2
    else:
        return 0.28


class ReviewEngine:
    """Drives the LLM-based review."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout,
        )

    async def review(
        self,
        *,
        repository: str,
        pr_title: str,
        pr_number: int,
        author: str,
        base_branch: str,
        head_branch: str,
        diff_text: str,
        static_findings: list[Finding],
        parsed_files: list[Any] | None = None,
    ) -> ReviewResult:
        truncated_diff = diff_text[: self.settings.max_chars]
        languages = "unknown"
        if parsed_files:
            from app.review.prompts import _detect_languages
            detected = _detect_languages(parsed_files)
            languages = ", ".join(sorted(detected)) if detected else "unknown"

        language_context = ""
        if parsed_files:
            language_context = build_language_context(parsed_files)

        user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
            repository=repository,
            pr_title=pr_title,
            pr_number=pr_number,
            author=author,
            base_branch=base_branch,
            head_branch=head_branch,
            languages=languages,
            max_chars=self.settings.max_chars,
            diff_text=truncated_diff,
            static_findings=format_static_findings(static_findings),
            few_shot_examples=FEW_SHOT_EXAMPLES if static_findings or len(truncated_diff) > 2000 else "",
        )

        system_prompt = REVIEW_SYSTEM_PROMPT.format(
            language_context=language_context,
        )

        schema = ReviewResult.model_json_schema()
        schema = _make_strict_schema(schema)

        temperature = _pick_temperature(len(truncated_diff), self.settings.openai_model)

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "review_result",
                        "schema": schema,
                        "strict": True,
                    },
                },
                temperature=temperature,
                max_tokens=4000,
            )
        except Exception as exc:
            logger.error("OpenAI request failed: %s", type(exc).__name__)
            raise LLMError("Failed to generate review from LLM.") from exc

        content = response.choices[0].message.content
        if not content:
            raise LLMError("OpenAI returned empty review content.")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("OpenAI returned invalid JSON: %s", exc)
            raise LLMError("LLM response was not valid JSON.") from exc

        try:
            result = ReviewResult.model_validate(data)
        except Exception as exc:
            logger.error("OpenAI response failed schema validation: %s", exc)
            raise LLMError(f"LLM response did not match ReviewResult schema: {exc}") from exc

        combined = list(static_findings) + list(result.findings)
        result.findings = combined
        return result

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
