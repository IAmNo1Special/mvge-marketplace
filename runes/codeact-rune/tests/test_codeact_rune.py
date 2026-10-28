from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from mvge.core.spells import SpellResult, SpellStatus
from mvge.runes.rune_api import RuneAPI
from mvge.runes.types import ExecutionMode, SpellDefinition
from mvge_runes_codeact.rune import CodeActExecutor, rune_factory


@pytest.fixture
def mock_api() -> Any:
    api = MagicMock(spec=RuneAPI)
    registered_spells: dict[str, SpellDefinition] = {}

    def register_spell(spell_def: SpellDefinition) -> None:
        registered_spells[spell_def.name] = spell_def

    def get_registered_spells() -> dict[str, SpellDefinition]:
        return dict(registered_spells)

    api.register_spell = register_spell
    api.get_registered_spells = get_registered_spells
    api.get_all_spells = lambda: list(registered_spells.values())
    api._registered = registered_spells
    return api


def test_codeact_rune_registration(mock_api: Any) -> None:
    executor = rune_factory(mock_api)
    assert isinstance(executor, CodeActExecutor)
    assert "execute_python" in mock_api._registered
    assert (
        mock_api._registered["execute_python"].execution_mode
        == ExecutionMode.SEQUENTIAL
    )


@pytest.mark.asyncio
async def test_codeact_executes_math_and_captures_last_expr(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    code = """
a = 15
b = 27
a + b
"""
    result = await executor.execute(code)
    assert result.status == SpellStatus.SUCCESS
    assert result.content == "42"
    assert result.terminate is False


@pytest.mark.asyncio
async def test_codeact_persists_session_locals(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res1 = await executor.execute("data_list = [1, 2, 3]")
    assert res1.status == SpellStatus.SUCCESS

    res2 = await executor.execute("sum(data_list)")
    assert res2.status == SpellStatus.SUCCESS
    assert res2.content == "6"


@pytest.mark.asyncio
async def test_codeact_rejects_forbidden_imports(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("import os\nos.system('echo hi')")
    assert res.status == SpellStatus.ERROR
    assert "Forbidden AST node" in (res.error_message or "")


@pytest.mark.asyncio
async def test_codeact_write_protection_against_shadowing_spells(mock_api: Any) -> None:
    # Register dummy spell 'read_file'
    mock_api.register_spell(
        SpellDefinition(
            name="read_file",
            description="read a file",
            parameters={},
            execution_mode=ExecutionMode.PARALLEL,
            handler=lambda *args, **kwargs: "file content",
        )
    )
    executor = CodeActExecutor(mock_api)

    # Attempt assignment to read_file
    res1 = await executor.execute("read_file = 'bad'")
    assert res1.status == SpellStatus.ERROR
    assert "Cannot overwrite protected spell" in (res1.error_message or "")

    # Attempt function def named read_file
    res2 = await executor.execute("def read_file(): pass")
    assert res2.status == SpellStatus.ERROR
    assert "Cannot overwrite protected spell" in (res2.error_message or "")

    # Attempt assignment to final_answer
    res3 = await executor.execute("final_answer = 100")
    assert res3.status == SpellStatus.ERROR
    assert "Cannot overwrite protected spell" in (res3.error_message or "")


@pytest.mark.asyncio
async def test_codeact_dispatches_spells(mock_api: Any) -> None:
    async def dummy_spell(
        cast_id: str, params: dict[str, Any], **kwargs: Any
    ) -> SpellResult:
        word = params.get("word", "")
        return SpellResult(
            spell_name="shout", status=SpellStatus.SUCCESS, content=f"{word.upper()}!"
        )

    mock_api.register_spell(
        SpellDefinition(
            name="shout",
            description="shouts word",
            parameters={"type": "object", "properties": {"word": {"type": "string"}}},
            execution_mode=ExecutionMode.PARALLEL,
            handler=dummy_spell,
        )
    )

    executor = CodeActExecutor(mock_api)
    code = """
greeting = shout(word='hello')
f"Got: {greeting}"
"""
    res = await executor.execute(code)
    assert res.status == SpellStatus.SUCCESS
    assert res.content == "Got: HELLO!"


@pytest.mark.asyncio
async def test_codeact_syntax_error_is_validation_error(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("def broken(")
    assert res.status == SpellStatus.ERROR
    assert "Validation error" in (res.error_message or "")


@pytest.mark.asyncio
async def test_codeact_runtime_errors(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("1 / 0")
    assert res.status == SpellStatus.ERROR
    assert "Runtime error" in (res.error_message or "")
    res2 = await executor.execute("undefined_name_xyz")
    assert res2.status == SpellStatus.ERROR
    assert "Runtime error" in (res2.error_message or "")


@pytest.mark.asyncio
async def test_codeact_rejects_forbidden_subprocess_import(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("import subprocess")
    assert res.status == SpellStatus.ERROR
    assert "Forbidden AST node" in (res.error_message or "")


@pytest.mark.asyncio
async def test_codeact_write_protection_assignment_forms(mock_api: Any) -> None:
    mock_api.register_spell(
        SpellDefinition(
            name="read_file",
            description="read a file",
            parameters={},
            execution_mode=ExecutionMode.PARALLEL,
            handler=lambda *args, **kwargs: "file content",
        )
    )
    executor = CodeActExecutor(mock_api)
    for code in (
        "read_file: int = 5",
        "read_file += 1",
        "(read_file := 5)",
        "(a, read_file) = (1, 2)",
    ):
        res = await executor.execute(code)
        assert res.status == SpellStatus.ERROR, code
        assert "Cannot overwrite protected spell" in (res.error_message or ""), code


@pytest.mark.asyncio
async def test_codeact_write_protection_def_forms(mock_api: Any) -> None:
    mock_api.register_spell(
        SpellDefinition(
            name="read_file",
            description="read a file",
            parameters={},
            execution_mode=ExecutionMode.PARALLEL,
            handler=lambda *args, **kwargs: "file content",
        )
    )
    executor = CodeActExecutor(mock_api)
    for code in (
        "async def read_file(): pass",
        "class read_file: pass",
    ):
        res = await executor.execute(code)
        assert res.status == SpellStatus.ERROR, code
        assert "Cannot overwrite protected spell" in (res.error_message or ""), code


@pytest.mark.asyncio
async def test_codeact_final_answer_direct(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("final_answer(42)")
    assert res.status == SpellStatus.SUCCESS
    assert res.content == "42"
    assert res.terminate is True


@pytest.mark.asyncio
async def test_codeact_no_trailing_expression_success_message(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res = await executor.execute("x = 5")
    assert res.status == SpellStatus.SUCCESS
    assert res.content == "Code executed successfully."
    assert res.terminate is False


@pytest.mark.asyncio
async def test_codeact_function_def_persists_across_executes(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    res1 = await executor.execute("def double(n):\n    return n * 2")
    assert res1.status == SpellStatus.SUCCESS
    res2 = await executor.execute("double(21)")
    assert res2.status == SpellStatus.SUCCESS
    assert res2.content == "42"


@pytest.mark.asyncio
async def test_codeact_dispatch_unknown_spell_raises(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    with pytest.raises(NameError, match="is not registered"):
        await executor.dispatch_spell("no_such_spell")


def test_codeact_registered_names_exclude_execute_python(mock_api: Any) -> None:
    mock_api.register_spell(
        SpellDefinition(
            name="shout",
            description="shouts",
            parameters={},
            execution_mode=ExecutionMode.PARALLEL,
            handler=lambda *args, **kwargs: "hi",
        )
    )
    executor = CodeActExecutor(mock_api)
    names = executor.get_registered_spell_names()
    assert "shout" in names
    assert "execute_python" not in names


@pytest.mark.asyncio
async def test_codeact_final_answer_terminates_turn(mock_api: Any) -> None:
    executor = CodeActExecutor(mock_api)
    code = """
answer = 10 * 10
final_answer(answer)
answer = 999  # Should not be reached
"""
    res = await executor.execute(code)
    assert res.status == SpellStatus.SUCCESS
    assert res.content == "100"
    assert res.terminate is True
