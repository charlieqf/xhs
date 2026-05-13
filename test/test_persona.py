"""单元测试：persona 加载、prompt 装配、forbidden 后过滤。

跑法（项目根目录）：
    python test/test_persona.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import persona  # noqa: E402


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def test_default_persona_loads_with_all_required_fields():
    p = persona.load("matchmaker_dongbei_38")
    for field in persona.REQUIRED_FIELDS:
        assert field in p, f"required field missing: {field}"
    assert isinstance(p["voice_constraints"], list) and len(p["voice_constraints"]) > 0
    assert isinstance(p["forbidden_phrases"], list) and len(p["forbidden_phrases"]) > 0


def test_load_raises_on_missing_file():
    try:
        persona.load("__nonexistent_persona__")
    except persona.PersonaError:
        return
    raise AssertionError("expected PersonaError for missing file")


def test_load_raises_on_missing_required_fields():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"name": "broken", "description": "missing other fields"}, f)
        tmp_path = f.name
    # 把临时文件挪到 persona dir
    target = os.path.join(ROOT, "prod", "personas", "_broken_test.json")
    try:
        os.replace(tmp_path, target)
        try:
            persona.load("_broken_test")
        except persona.PersonaError:
            return
        raise AssertionError("expected PersonaError for incomplete persona")
    finally:
        if os.path.exists(target):
            os.remove(target)


# ---------------------------------------------------------------------------
# Prompt 装配
# ---------------------------------------------------------------------------

def test_build_system_message_includes_identity_and_json_constraint():
    p = persona.load("matchmaker_dongbei_38")
    msg = persona.build_system_message(p)
    assert p["system_identity"] in msg
    assert "JSON" in msg or "json" in msg


def test_build_voice_block_contains_constraints_and_forbidden():
    p = persona.load("matchmaker_dongbei_38")
    block = persona.build_voice_block(p)
    assert "业务情境" in block
    # 至少有一条 voice constraint 出现
    assert any(v in block for v in p["voice_constraints"])
    # 关键禁用词应该出现在 block 里（让 LLM 看到）
    assert "私我" in block and "同频" in block


def test_llm_model_returns_persona_model_or_default():
    p = persona.load("matchmaker_dongbei_38")
    assert persona.llm_model(p) == "google/gemini-3-flash-preview"

    p_no_llm = {k: v for k, v in p.items() if k != "llm"}
    assert persona.llm_model(p_no_llm) == "google/gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# Post-filter
# ---------------------------------------------------------------------------

def test_find_forbidden_detects_hits_and_misses():
    p = persona.load("matchmaker_dongbei_38")

    clean = "我之前也整不明白这种事，多看看就懂了"
    assert persona.find_forbidden(clean, p) == []

    dirty = "可以私我聊聊，同频的人都在这"
    hits = persona.find_forbidden(dirty, p)
    assert "私我" in hits
    assert "同频" in hits


def test_find_forbidden_handles_empty_input():
    p = persona.load("matchmaker_dongbei_38")
    assert persona.find_forbidden("", p) == []
    assert persona.find_forbidden(None, p) == []


def test_scrub_or_regenerate_passthrough_when_clean():
    p = persona.load("matchmaker_dongbei_38")
    result = {"selected_index": 1, "reason": "x", "generated_reply": "我之前也走过这步"}
    cleaned, status = persona.scrub_or_regenerate(result, p)
    assert status == "ok"
    assert cleaned == result


def test_scrub_or_regenerate_calls_regenerate_when_dirty():
    p = persona.load("matchmaker_dongbei_38")
    dirty = {"selected_index": 1, "reason": "x", "generated_reply": "可以私我看看"}

    call_count = {"n": 0}
    def regen():
        call_count["n"] += 1
        return {"selected_index": 1, "reason": "x", "generated_reply": "可以看我主页"}

    cleaned, status = persona.scrub_or_regenerate(dirty, p, regen)
    assert status == "regenerated"
    assert cleaned["generated_reply"] == "可以看我主页"
    assert call_count["n"] == 1


def test_scrub_or_regenerate_drops_when_regenerate_still_dirty():
    p = persona.load("matchmaker_dongbei_38")
    dirty = {"selected_index": 1, "reason": "x", "generated_reply": "私我聊聊"}

    def regen():
        return {"selected_index": 1, "reason": "x", "generated_reply": "私我吧"}

    cleaned, status = persona.scrub_or_regenerate(dirty, p, regen)
    assert status == "dropped"
    assert cleaned is None


def test_scrub_or_regenerate_drops_when_no_regenerate_fn_supplied():
    p = persona.load("matchmaker_dongbei_38")
    dirty = {"selected_index": 1, "reason": "x", "generated_reply": "私我"}
    cleaned, status = persona.scrub_or_regenerate(dirty, p, regenerate_fn=None)
    assert status == "dropped"
    assert cleaned is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_default_persona_loads_with_all_required_fields,
    test_load_raises_on_missing_file,
    test_load_raises_on_missing_required_fields,
    test_build_system_message_includes_identity_and_json_constraint,
    test_build_voice_block_contains_constraints_and_forbidden,
    test_llm_model_returns_persona_model_or_default,
    test_find_forbidden_detects_hits_and_misses,
    test_find_forbidden_handles_empty_input,
    test_scrub_or_regenerate_passthrough_when_clean,
    test_scrub_or_regenerate_calls_regenerate_when_dirty,
    test_scrub_or_regenerate_drops_when_regenerate_still_dirty,
    test_scrub_or_regenerate_drops_when_no_regenerate_fn_supplied,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR   {fn.__name__}: {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"{failed}/{len(TESTS)} failed")
        return 1
    print(f"all {len(TESTS)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
