from __future__ import annotations

import ast
import builtins
import inspect
import uuid
from typing import Any

from mvgeos_core.sandbox import MvgeSandbox
from mvgeos_core.spells import (
    ExecutionMode,
    MvgeSpell,
    SpellContext,
    SpellResult,
    SpellStatus,
)
from mvgeos_runes.rune_api import RuneAPI
from mvgeos_runes.types import SpellDefinition

SAFE_STDLIB_MODULES = {
    "math",
    "json",
    "re",
    "datetime",
    "random",
    "collections",
    "itertools",
    "functools",
    "string",
    "copy",
    "heapq",
    "bisect",
    "typing",
}


class _FinalAnswerException(Exception):
    def __init__(self, value: Any) -> None:
        super().__init__(str(value))
        self.value = value


class _WriteProtectionVisitor(ast.NodeVisitor):
    def __init__(self, protected_names: set[str]) -> None:
        self.protected_names = protected_names

    def _check_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if target.id in self.protected_names:
                raise ValueError(
                    f"Cannot overwrite protected spell or keyword '{target.id}'"
                )
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._check_target(elt)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in self.protected_names:
            raise ValueError(
                f"Cannot overwrite protected spell or keyword '{node.name}'"
            )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name in self.protected_names:
            raise ValueError(
                f"Cannot overwrite protected spell or keyword '{node.name}'"
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name in self.protected_names:
            raise ValueError(
                f"Cannot overwrite protected spell or keyword '{node.name}'"
            )
        self.generic_visit(node)


class _CodeActTransformer(ast.NodeTransformer):
    def __init__(self, spell_names: set[str]) -> None:
        self.spell_names = spell_names

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name == "final_answer":
                return ast.Call(
                    func=ast.Name(id="__final_answer__", ctx=ast.Load()),
                    args=node.args,
                    keywords=node.keywords,
                )
            if func_name in self.spell_names:
                dispatch_call = ast.Call(
                    func=ast.Name(id="__mvge_dispatch__", ctx=ast.Load()),
                    args=[ast.Constant(value=func_name), *node.args],
                    keywords=node.keywords,
                )
                return ast.Await(value=dispatch_call)
        return node


class CodeActExecutor:
    """Executes Python code with AST protection, spell dispatching, and session locals."""

    def __init__(self, api: RuneAPI) -> None:
        self.api = api
        self.sandbox = MvgeSandbox()
        self.session_locals: dict[str, Any] = {}

    def get_registered_spell_names(self) -> set[str]:
        spells = {s.name: s for s in self.api.get_all_spells()}
        names = set(spells.keys())
        runner = getattr(self.api, "_runner", None)
        if runner is not None:
            disc = getattr(runner, "_discoverable_builtin_spells", {})
            if isinstance(disc, dict):
                names.update(disc.keys())
        names.discard("execute_python")
        return names

    async def dispatch_spell(
        self,
        spell_name: str,
        *args: Any,
        ctx: SpellContext | None = None,
        **kwargs: Any,
    ) -> Any:
        spells = {s.name: s for s in self.api.get_all_spells()}
        spell: SpellDefinition | MvgeSpell | None = spells.get(spell_name)
        if spell is None:
            runner = getattr(self.api, "_runner", None)
            if runner is not None and hasattr(runner, "get_spell"):
                spell = runner.get_spell(spell_name)

        if spell is None:
            raise NameError(f"Spell '{spell_name}' is not registered.")

        # Map positional arguments to keyword arguments using inspect.signature if needed
        merged_kwargs = dict(kwargs)
        raw_handler = getattr(spell, "handler", getattr(spell, "func", spell.execute))
        try:
            sig = inspect.signature(raw_handler)
            param_names = [
                name
                for name, p in sig.parameters.items()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
                and name not in ("ctx", "spell_context", "signal", "params")
            ]
            for i, arg in enumerate(args):
                if i < len(param_names):
                    merged_kwargs[param_names[i]] = arg
        except (ValueError, TypeError):
            pass

        cast_id = str(uuid.uuid4())
        call_kwargs: dict[str, Any] = {}
        if ctx is not None:
            call_kwargs["signal"] = ctx.signal
            call_kwargs["ctx"] = ctx

        res: Any
        if inspect.iscoroutinefunction(spell.execute):
            res = await spell.execute(cast_id, merged_kwargs, **call_kwargs)
        else:
            raw_res = spell.execute(cast_id, merged_kwargs, **call_kwargs)
            if inspect.isawaitable(raw_res):
                res = await raw_res
            else:
                res = raw_res

        if isinstance(res, SpellResult):
            return res.content
        return res

    def transform_code(
        self, code_str: str, spell_names: set[str]
    ) -> tuple[ast.Module, set[str]]:
        # Validate AST safety
        parsed = self.sandbox.validate_ast(
            code_str, allowed_modules=SAFE_STDLIB_MODULES
        )

        # Write protection check
        protected = set(spell_names) | {"final_answer", "__mvge_dispatch__"}
        _WriteProtectionVisitor(protected).visit(parsed)

        # Transform spell calls to await __mvge_dispatch__
        transformer = _CodeActTransformer(spell_names)
        transformed = transformer.visit(parsed)
        ast.fix_missing_locations(transformed)

        body = list(transformed.body)
        top_names: set[str] = set()
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        top_names.add(t.id)
            elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(stmt.target, ast.Name):
                    top_names.add(stmt.target.id)
            elif isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                top_names.add(stmt.name)

        if body and isinstance(body[-1], ast.Expr):
            last_expr = body[-1].value
            assign_last = ast.Assign(
                targets=[ast.Name(id="__last_expr__", ctx=ast.Store())],
                value=last_expr,
            )
            body[-1] = assign_last

        init_last = ast.Assign(
            targets=[ast.Name(id="__last_expr__", ctx=ast.Store())],
            value=ast.Constant(value=None),
        )

        global_stmt = ast.Global(names=sorted(top_names | {"__last_expr__"}))
        func_body = [
            global_stmt,
            init_last,
            *body,
            ast.Return(value=ast.Name(id="__last_expr__", ctx=ast.Load())),
        ]

        # Wrap in async def __codeact_runner__(__mvge_dispatch__, __final_answer__):
        func_def = ast.AsyncFunctionDef(
            name="__codeact_runner__",
            args=ast.arguments(
                posonlyargs=[],
                args=[
                    ast.arg(arg="__mvge_dispatch__"),
                    ast.arg(arg="__final_answer__"),
                ],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=func_body,
            decorator_list=[],
            returns=None,
        )

        mod = ast.Module(body=[func_def], type_ignores=[])
        ast.fix_missing_locations(mod)
        return mod, top_names

    async def execute(
        self,
        code: str,
        ctx: SpellContext | None = None,
    ) -> SpellResult:
        spell_names = self.get_registered_spell_names()

        try:
            mod, top_names = self.transform_code(code, spell_names)
            compiled = compile(mod, filename="<codeact>", mode="exec")
        except (ValueError, SyntaxError) as exc:
            return SpellResult(
                spell_name="execute_python",
                status=SpellStatus.ERROR,
                error_message=f"Validation error: {exc}",
            )

        async def _dispatch_wrapper(s_name: str, *s_args: Any, **s_kwargs: Any) -> Any:
            return await self.dispatch_spell(s_name, *s_args, ctx=ctx, **s_kwargs)

        def _final_answer_wrapper(val: Any) -> None:
            raise _FinalAnswerException(val)

        # Prepare scope
        exec_globals: dict[str, Any] = dict(self.session_locals)
        exec_globals["__builtins__"] = {
            k: getattr(builtins, k)
            for k in (
                "abs",
                "all",
                "any",
                "bin",
                "bool",
                "bytes",
                "bytearray",
                "callable",
                "chr",
                "complex",
                "dict",
                "divmod",
                "enumerate",
                "filter",
                "float",
                "format",
                "frozenset",
                "getattr",
                "hasattr",
                "hash",
                "hex",
                "id",
                "int",
                "isinstance",
                "issubclass",
                "iter",
                "len",
                "list",
                "locals",
                "map",
                "max",
                "min",
                "next",
                "oct",
                "ord",
                "pow",
                "print",
                "range",
                "repr",
                "reversed",
                "round",
                "set",
                "slice",
                "sorted",
                "str",
                "sum",
                "tuple",
                "type",
                "vars",
                "zip",
                "Exception",
                "ValueError",
                "TypeError",
                "KeyError",
                "IndexError",
                "AttributeError",
                "RuntimeError",
                "True",
                "False",
                "None",
            )
            if hasattr(builtins, k)
        }

        try:
            exec(compiled, exec_globals)  # noqa: S102
            runner_fn = exec_globals["__codeact_runner__"]
            res = await runner_fn(_dispatch_wrapper, _final_answer_wrapper)

            # Persist defined top-level variables
            for k in top_names:
                if k in exec_globals:
                    self.session_locals[k] = exec_globals[k]

            output = str(res) if res is not None else "Code executed successfully."
            return SpellResult(
                spell_name="execute_python",
                status=SpellStatus.SUCCESS,
                content=output,
            )
        except _FinalAnswerException as fa:
            return SpellResult(
                spell_name="execute_python",
                status=SpellStatus.SUCCESS,
                content=str(fa.value),
                terminate=True,
            )
        except Exception as exc:  # noqa: BLE001
            return SpellResult(
                spell_name="execute_python",
                status=SpellStatus.ERROR,
                error_message=f"Runtime error: {exc}",
            )


def rune_factory(api: RuneAPI) -> CodeActExecutor:
    executor = CodeActExecutor(api)

    async def execute_python_handler(
        code: str,
        ctx: SpellContext | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> SpellResult:
        return await executor.execute(code, ctx=ctx)

    api.register_spell(
        SpellDefinition(
            name="execute_python",
            description=(
                "Execute Python code in a safe CodeAct sandbox with direct access to "
                "registered spells and final_answer(value) termination."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute.",
                    }
                },
                "required": ["code"],
            },
            execution_mode=ExecutionMode.SEQUENTIAL,
            handler=execute_python_handler,
        )
    )

    return executor
