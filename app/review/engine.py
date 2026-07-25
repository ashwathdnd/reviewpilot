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
                # Resolve $ref
                if "$ref" in prop:
                    resolved = _resolve_ref(prop["$ref"])
                    prop.clear()
                    prop.update(resolved)

                if "anyOf" in prop:
                    # Resolve any $ref inside anyOf items before extracting types.
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
                        # Enums have "enum" but no explicit type; infer type from first
                        # value if one exists.
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

                # Recurse into nested objects
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
    ) -> ReviewResult:
        user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
            repository=repository,
            pr_title=pr_title,
            pr_number=pr_number,
            author=author,
            base_branch=base_branch,
            head_branch=head_branch,
            max_chars=self.settings.max_chars,
            diff_text=diff_text[: self.settings.max_chars],
            static_findings=format_static_findings(static_findings),
        )

        schema = ReviewResult.model_json_schema()
        schema = _make_strict_schema(schema)

        try:
            response = await self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
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
                temperature=0.2,
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

        # Merge static findings with LLM findings. Static checks run first, then
        # validator deduplicates and ranks the combined set.
        combined = list(static_findings) + list(result.findings)
        result.findings = combined
        return result

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
