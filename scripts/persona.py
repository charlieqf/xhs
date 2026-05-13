"""Persona loading + LLM-prompt assembly + forbidden-phrase post-filter.

Personas live at ``prod/personas/<persona_name>.json``. Each persona supplies
the speaker's identity, voice constraints, forbidden phrases, and emoji policy
for the LLM that generates comment replies. The point is to break the
""SERVICE_DESC + 'be a salesperson'"" prompt that produces high-DNA-overlap
replies (see ``docs/anti-detection-phase1-testing-2026-05-13.md`` problem D).

Typical flow from a bot::

    p = persona.load("matchmaker_dongbei_38")
    system_msg, user_prompt_voice_block = persona.build_prompt_blocks(p)
    # bot assembles full user prompt: task + voice_block + comments
    ...
    reply = call_llm(system_msg, full_user_prompt)
    cleaned = persona.scrub_or_regenerate(reply, p, regenerate_fn)

Schema (see ``prod/personas/matchmaker_dongbei_38.json`` for full example)::

    {
      "name": str,                            # identifier matching filename
      "description": str,                     # human-readable, NOT injected into prompts
      "system_identity": str,                 # LLM system role text
      "business_context": str,                # business framing in persona voice
      "voice_constraints": list[str],         # bullet list joined into prompt
      "forbidden_phrases": list[str],         # post-filter substrings
      "preferred_register_hints": list[str],  # optional vocabulary hints
      "emoji_policy": str,                    # short instruction
      "llm": {"provider": str, "model": str}, # for future multi-LLM mix
      "on_forbidden_match": "regenerate_once" # or "skip"
    }
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERSONA_DIR = os.path.join(_PROJECT_ROOT, "prod", "personas")

REQUIRED_FIELDS = (
    "name",
    "system_identity",
    "business_context",
    "voice_constraints",
    "forbidden_phrases",
)

DEFAULT_PERSONA = "matchmaker_dongbei_38"


class PersonaError(RuntimeError):
    """Raised when a persona file is missing required fields or malformed."""


# ---------------------------------------------------------------------------
#  Loading
# ---------------------------------------------------------------------------

def persona_path(persona_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in persona_name)
    return os.path.join(_PERSONA_DIR, f"{safe}.json")


def load(persona_name: str = DEFAULT_PERSONA) -> dict[str, Any]:
    path = persona_path(persona_name)
    if not os.path.exists(path):
        raise PersonaError(f"persona file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise PersonaError(
            f"persona '{persona_name}' missing required fields: {missing}"
        )
    return data


# ---------------------------------------------------------------------------
#  Prompt assembly
# ---------------------------------------------------------------------------

def build_system_message(persona: dict[str, Any]) -> str:
    """LLM 系统角色：身份 + JSON 输出约束。"""
    return (
        f"{persona['system_identity']}\n\n"
        "你的输出必须是合法的 JSON，符合后面给的 schema。不要返回 JSON 以外的任何内容。"
    )


def build_voice_block(persona: dict[str, Any]) -> str:
    """组装 voice / business context / forbidden / emoji 约束块，给 user prompt 用。"""
    parts = [f"业务情境：{persona['business_context']}"]

    parts.append("回复风格要求：")
    for v in persona["voice_constraints"]:
        parts.append(f"  - {v}")

    if persona.get("preferred_register_hints"):
        hints = "、".join(persona["preferred_register_hints"])
        parts.append(f"可以适度使用的表达：{hints}")

    if persona["forbidden_phrases"]:
        forbidden = "、".join(f"'{p}'" for p in persona["forbidden_phrases"])
        parts.append(f"严格不要出现以下词或近义表述：{forbidden}")

    if persona.get("anti_examples"):
        parts.append("以下是反面示例（**不要**写成这种风格）：")
        for ex in persona["anti_examples"]:
            parts.append(f"  ✗ {ex}")

    if persona.get("emoji_policy"):
        parts.append(f"Emoji 策略：{persona['emoji_policy']}")

    return "\n".join(parts)


def llm_model(persona: dict[str, Any]) -> str:
    return persona.get("llm", {}).get("model", "google/gemini-3-flash-preview")


# ---------------------------------------------------------------------------
#  Post-filter
# ---------------------------------------------------------------------------

def find_forbidden(reply_text: str, persona: dict[str, Any]) -> list[str]:
    """Return list of forbidden phrases that appear in ``reply_text``."""
    if not reply_text:
        return []
    return [p for p in persona["forbidden_phrases"] if p and p in reply_text]


def scrub_or_regenerate(
    llm_result: Optional[dict[str, Any]],
    persona: dict[str, Any],
    regenerate_fn: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """Apply forbidden-phrase post-filter.

    Returns ``(cleaned_result_or_None, status)`` where ``status`` is one of:
      ``"ok"`` — reply passed first time
      ``"regenerated"`` — first reply had forbidden, regenerate produced clean
      ``"dropped"`` — even after regenerate (or with skip policy), still dirty;
                       caller should treat as "no acceptable reply"

    If ``regenerate_fn`` is None and the first reply is dirty, returns
    ``(None, "dropped")``.
    """
    if not llm_result:
        return llm_result, "ok"

    reply = llm_result.get("generated_reply", "") or ""
    hits = find_forbidden(reply, persona)
    if not hits:
        return llm_result, "ok"

    policy = persona.get("on_forbidden_match", "regenerate_once")

    if policy == "regenerate_once" and regenerate_fn is not None:
        second = regenerate_fn()
        if second:
            second_reply = second.get("generated_reply", "") or ""
            if not find_forbidden(second_reply, persona):
                return second, "regenerated"
        return None, "dropped"

    return None, "dropped"
