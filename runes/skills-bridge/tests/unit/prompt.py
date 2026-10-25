from __future__ import annotations

from dataclasses import dataclass

from mvge_runes_skills_bridge.prompt import (
    build_skill_catalog,
    update_invocations_with_skill_catalog,
)
from mvge_runes_skills_bridge.types import SkillManifest


@dataclass
class DummyInvocation:
    role: str
    content: str


def test_build_skill_catalog_empty_or_suppressed() -> None:
    assert build_skill_catalog([]) == ""
    m = SkillManifest(name="s1", description="d1", path="/p/s1")
    assert build_skill_catalog([m], suppress=True) == ""


def test_build_skill_catalog_filters_disabled_invocation() -> None:
    m1 = SkillManifest(
        name="s1", description="d1", path="/p/s1", disable_model_invocation=True
    )
    m2 = SkillManifest(
        name="s2", description="d2", path="/p/s2", disable_model_invocation=False
    )
    xml = build_skill_catalog([m1, m2])
    assert "<available_skills>" in xml
    assert "<name>s2</name>" in xml
    assert "<name>s1</name>" not in xml


def test_update_invocations_with_skill_catalog_dataclass() -> None:
    m = SkillManifest(name="test-skill", description="desc", path="/tmp/test-skill")
    invocations = [
        DummyInvocation(role="user", content="Initial user prompt"),
    ]

    # First turn: appends catalog
    res1 = update_invocations_with_skill_catalog(invocations, [m])
    assert len(res1) == 1
    assert "<available_skills>" in res1[0].content
    assert "<name>test-skill</name>" in res1[0].content

    # Second turn with updated skills: replaces in-place without duplicating
    m2 = SkillManifest(
        name="second-skill", description="desc2", path="/tmp/second-skill"
    )
    res2 = update_invocations_with_skill_catalog(res1, [m2])
    assert len(res2) == 1
    assert res2[0].content.count("<available_skills>") == 1
    assert "<name>second-skill</name>" in res2[0].content
    assert "<name>test-skill</name>" not in res2[0].content


def test_update_invocations_with_skill_catalog_dicts() -> None:
    m = SkillManifest(name="dict-skill", description="desc", path="/tmp/dict-skill")
    invocations = [
        {"role": "user", "content": "Hello world"},
    ]

    res1 = update_invocations_with_skill_catalog(invocations, [m])
    assert "<available_skills>" in res1[0]["content"]

    # Suppress catalog removes in-place
    res2 = update_invocations_with_skill_catalog(res1, [m], suppress=True)
    assert "<available_skills>" not in res2[0]["content"]
