"""Static checks on step and pipeline source, without executing it.

Two analyses, both over Python's ``ast``:

* :func:`check_step` looks at one ``@artifact_task`` function: literal returns against the
  declared outputs, names and defaults, and likely hidden inputs (reads, env, clock, random).
* :func:`pipeline_shape` turns a ``@pipeline`` body into a dataflow graph — step calls as
  nodes, values passed between them as edges — marking what parsing can't decide: steps under
  an ``if`` (conditional), in a loop (repeated) and calls it can't resolve (dynamic).

Neither ever runs the analysed code or feeds an execution signature; ``Experiment.plan()``
remains the authority on what one experiment actually does.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import itertools
import textwrap
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .hashing import canonical_json


class StepDefinitionWarning(UserWarning):
    """A step or pipeline declaration looks wrong (see the diagnostic code in the message)."""


@dataclass
class Diagnostic:
    code: str
    severity: str  # "error" | "warning"
    message: str
    line: int | None = None  # 1-based, relative to the analysed source text
    col: int | None = None
    step: str | None = None
    file: str | None = None
    file_line: int | None = None  # line in ``file``

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        where = f"{self.file or '<source>'}:{self.file_line or self.line or 0}"
        return f"{where}: {self.severity} {self.code}: {self.message}"


def function_source(fn: Callable) -> tuple[str, int, str | None] | None:
    """(dedented source, first line in its file, file), or None when it can't be retrieved."""
    try:
        lines, first = inspect.getsourcelines(fn)
        file = inspect.getsourcefile(fn)
    except (OSError, TypeError):
        return None
    return textwrap.dedent("".join(lines)), first, file


def _parse_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        mod = ast.parse(source)
    except SyntaxError:
        return None
    for node in mod.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _own_nodes(func: ast.AST):
    """Nodes of a function body, not descending into nested functions, lambdas or classes."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


# -- step checks ------------------------------------------------------------------------------
_CLOCK = {
    "time.time", "time.time_ns", "time.monotonic", "time.perf_counter",
    "datetime.now", "datetime.utcnow", "datetime.today", "date.today",
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.date.today",
}
_RANDOM_PREFIXES = ("random.", "np.random.", "numpy.random.")
_SEEDING = {"seed", "Random", "default_rng", "RandomState", "SystemRandom", "Generator"}
_READ_METHODS = {"read_text", "read_bytes", "open"}


def check_step(task: Any) -> list[Diagnostic]:
    """Diagnostics for one ``ArtifactTask`` (see the module docstring)."""
    fn, outputs, name = task.fn, list(task.outputs), task.name
    found: list[Diagnostic] = []
    for out in outputs:
        if not out.isidentifier():
            found.append(Diagnostic("bad-output-name", "error", f"output name {out!r} is not a valid identifier", step=name))
    for pname, p in task.signature.parameters.items():
        if p.default is not inspect.Parameter.empty:
            try:
                canonical_json(p.default)
            except TypeError:
                found.append(Diagnostic(
                    "default-not-json", "error",
                    f"default of {pname!r} ({type(p.default).__name__}) can't be part of a signature; "
                    "use a JSON value, or pass the object in as an artifact",
                    step=name,
                ))
    src = function_source(fn)
    func = _parse_function(src[0]) if src else None
    if func is not None:
        found += _check_returns(func, outputs, name)
        found += _check_hidden_inputs(func, set(task.signature.parameters), name)
        source, first, file = src
        for d in found:
            d.file = file
            if d.line is not None:
                d.file_line = d.line + first - 1
    ignore = set(getattr(task, "ignore", ()))
    return [d for d in found if d.code not in ignore]


def _check_returns(func: ast.AST, outputs: list[str], step: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    nodes = list(_own_nodes(func))
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in nodes):
        return out  # generators are their own story
    returns = [n for n in nodes if isinstance(n, ast.Return)]
    body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    trivial = all(isinstance(s, (ast.Pass, ast.Raise)) for s in body)
    valued = [r for r in returns if r.value is not None and not (isinstance(r.value, ast.Constant) and r.value.value is None)]
    if not valued and not trivial:
        out.append(Diagnostic(
            "no-return", "error",
            f"never returns a value, but declares output{'s' if len(outputs) > 1 else ''} {', '.join(outputs)}",
            line=func.lineno, col=func.col_offset, step=step,
        ))
    n = len(outputs)
    for r in valued:
        v = r.value
        msg = None
        if n > 1 and isinstance(v, ast.Tuple) and not any(isinstance(e, ast.Starred) for e in v.elts) and len(v.elts) != n:
            msg = f"returns {len(v.elts)} values, but declares {n} outputs ({', '.join(outputs)})"
        elif n > 1 and isinstance(v, ast.Dict) and v.keys and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in v.keys):
            keys = [k.value for k in v.keys]  # type: ignore[union-attr]
            if set(keys) != set(outputs):
                missing, extra = sorted(set(outputs) - set(keys)), sorted(set(keys) - set(outputs))
                parts = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"unexpected {', '.join(extra)}"] if extra else [])
                msg = f"returns a dict whose keys don't match the declared outputs: {'; '.join(parts)}"
        elif n > 1 and isinstance(v, (ast.Constant, ast.List, ast.Set, ast.ListComp, ast.SetComp, ast.JoinedStr)):
            msg = f"returns a single value, but declares {n} outputs ({', '.join(outputs)}); return a tuple or a dict"
        if msg:
            out.append(Diagnostic("return-shape", "error", msg, line=r.lineno, col=r.col_offset, step=step))
    return out


def _check_hidden_inputs(func: ast.AST, params: set[str], step: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    seen: set[tuple[str, int]] = set()

    def add(code: str, node: ast.AST, msg: str) -> None:
        key = (code, node.lineno)  # type: ignore[attr-defined]
        if key not in seen:
            seen.add(key)
            out.append(Diagnostic(code, "warning", msg, line=node.lineno, col=node.col_offset, step=step))  # type: ignore[attr-defined]

    seeded = any(p == "seed" or p.endswith("_seed") for p in params)
    for node in _own_nodes(func):
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "os.environ":
            add("hidden-env", node, "reads an environment variable the cache can't see; make it a param, or put it in DENDRITE_ENV")
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func) or ""
        literal = bool(node.args) and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        if name == "open" and literal:
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if not (isinstance(mode, ast.Constant) and any(c in str(mode.value) for c in "wax+")):
                add("hidden-read", node, f"reads {node.args[0].value!r}, which is not a declared input; pass it in as an artifact")  # type: ignore[union-attr]
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _READ_METHODS:
            inner = node.func.value
            if isinstance(inner, ast.Call) and (_dotted(inner.func) or "").split(".")[-1] in ("Path", "PurePath") and inner.args \
                    and isinstance(inner.args[0], ast.Constant) and isinstance(inner.args[0].value, str):
                add("hidden-read", node, f"reads {inner.args[0].value!r}, which is not a declared input; pass it in as an artifact")
        if name in ("os.getenv", "os.environ.get", "getenv"):
            add("hidden-env", node, "reads an environment variable the cache can't see; make it a param, or put it in DENDRITE_ENV")
        elif name in _CLOCK:
            add("hidden-clock", node, f"{name}() makes the output depend on when it ran; the cache will reuse the first result")
        elif name.startswith(_RANDOM_PREFIXES) and name.split(".")[-1] not in _SEEDING and not seeded:
            add("unseeded-random", node, f"{name}() without a seed param: reruns won't reproduce, but the cache will reuse; add a `seed` param")
    return out


# -- pipeline shape ---------------------------------------------------------------------------
@dataclass
class _Val:
    """What a variable may hold: producers (atoms) and, for tuples, per-element values."""

    atoms: list[tuple] = field(default_factory=list)  # ("input", name) | ("out", node_id, output)
    elts: list["_Val"] | None = None
    origin: tuple[str, int] | None = None  # (step, number of outputs) for unpacking checks

    @staticmethod
    def union(*vals: "_Val | None") -> "_Val":
        atoms: list[tuple] = []
        for v in vals:
            for a in (v.atoms if v else []):
                if a not in atoms:
                    atoms.append(a)
        tuples = [v.elts for v in vals if v and v.elts is not None]
        elts = None
        if tuples and len({len(t) for t in tuples}) == 1 and len(tuples) == len([v for v in vals if v]):
            elts = [_Val.union(*col) for col in zip(*tuples)]
        origins = {v.origin for v in vals if v and v.origin}
        return _Val(atoms, elts, origins.pop() if len(origins) == 1 else None)


_UNKNOWN = _Val()


def _is_step(obj: Any) -> bool:
    return hasattr(obj, "outputs") and hasattr(obj, "signature") and hasattr(obj, "_prefect_task")


class _ShapeBuilder:
    def __init__(self, source: str, resolve: Callable[[str], Any], module: str | None):
        self.source = source
        self.resolve = resolve
        self.module = module
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.diags: list[Diagnostic] = []
        self.calls: dict[str, list[tuple]] = {}  # node -> ctx paths of each call
        self.ctx: list[tuple] = []  # ("if", id, arm, text) | ("loop",)
        self.if_ids = itertools.count()
        self.depth = 0  # helper expansion depth
        self.steps: dict[str, Any] = {}

    # ---- helpers
    def seg(self, node: ast.AST) -> str:
        return (ast.get_source_segment(self.source, node) or "").strip()

    def diag(self, code: str, node: ast.AST, msg: str, severity: str = "error", step: str | None = None) -> None:
        self.diags.append(Diagnostic(code, severity, msg, line=getattr(node, "lineno", None), col=getattr(node, "col_offset", None), step=step))

    def edge(self, atom: tuple, to: str, input_name: str) -> None:
        if atom[0] == "input":
            e = {"from": f"input:{atom[1]}", "to": to, "label": atom[1], "input": input_name}
        else:
            e = {"from": atom[1], "to": to, "label": atom[2], "input": input_name}
        if e not in self.edges:
            self.edges.append(e)

    def stage_name(self, e: ast.AST) -> str | None:
        """The literal name of a `stage("…")` / `dendrite.stage("…")` call, else None."""
        if not (isinstance(e, ast.Call) and e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str)):
            return None
        f = e.func
        if isinstance(f, ast.Attribute) and f.attr == "stage":
            return e.args[0].value
        if isinstance(f, ast.Name):
            obj = self.resolve(f.id)
            if getattr(obj, "__name__", None) == "stage" and getattr(obj, "__module__", "").startswith("dendrite"):
                return e.args[0].value
        return None

    def stages(self) -> list[str]:
        return [f[1] for f in self.ctx if f[0] == "stage"]

    def conditions(self) -> list[str]:
        return [f[3] for f in self.ctx if f[0] == "if"]

    def in_loop(self) -> bool:
        return any(f[0] == "loop" for f in self.ctx)

    # ---- statements
    def run_body(self, body: list[ast.stmt], env: dict[str, _Val]) -> list[_Val]:
        returned: list[_Val] = []
        for stmt in body:
            returned += self.stmt(stmt, env)
        return returned

    def stmt(self, s: ast.stmt, env: dict[str, _Val]) -> list[_Val]:
        if isinstance(s, ast.Assign):
            v = self.expr(s.value, env)
            for t in s.targets:
                self.assign(t, v, env, s)
        elif isinstance(s, (ast.AnnAssign, ast.AugAssign)):
            if s.value is not None:
                v = self.expr(s.value, env)
                if isinstance(s.target, ast.Name):
                    env[s.target.id] = v if isinstance(s, ast.AnnAssign) else _Val.union(env.get(s.target.id), v)
        elif isinstance(s, ast.Expr):
            self.expr(s.value, env)
        elif isinstance(s, ast.Return):
            return [self.expr(s.value, env)] if s.value is not None else []
        elif isinstance(s, ast.If):
            text = self.seg(s.test)
            self.expr(s.test, env)
            if_id = next(self.if_ids)
            body_env, else_env = dict(env), dict(env)
            self.ctx.append(("if", if_id, "body", text))
            ret = self.run_body(s.body, body_env)
            self.ctx[-1] = ("if", if_id, "orelse", f"not ({text})")
            ret += self.run_body(s.orelse, else_env)
            self.ctx.pop()
            for k in set(body_env) | set(else_env):
                a, b = body_env.get(k), else_env.get(k)
                env[k] = a if a is b else _Val.union(a, b)
            return ret
        elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            if isinstance(s, ast.While):
                self.expr(s.test, env)
            else:
                it = self.expr(s.iter, env)
                self.assign(s.target, _Val(list(it.atoms)), env, s)
            self.ctx.append(("loop",))
            ret = self.run_body(s.body, env)
            self.ctx.pop()
            return ret + self.run_body(s.orelse, env)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            pushed = 0
            for item in s.items:
                name = self.stage_name(item.context_expr)
                if name is not None:  # `with dendrite.stage("COLMAP"):` groups the steps inside
                    self.ctx.append(("stage", name))
                    pushed += 1
                    continue
                v = self.expr(item.context_expr, env)
                if item.optional_vars is not None:
                    self.assign(item.optional_vars, v, env, s)
            try:
                return self.run_body(s.body, env)
            finally:
                for _ in range(pushed):
                    self.ctx.pop()
        elif isinstance(s, ast.Try) or type(s).__name__ == "TryStar":
            ret = self.run_body(s.body, env)  # type: ignore[attr-defined]
            for h in s.handlers:  # type: ignore[attr-defined]
                ret += self.run_body(h.body, env)
            return ret + self.run_body(s.orelse, env) + self.run_body(s.finalbody, env)  # type: ignore[attr-defined]
        return []

    def assign(self, target: ast.AST, v: _Val, env: dict[str, _Val], at: ast.AST) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = v
        elif isinstance(target, (ast.Tuple, ast.List)):
            names = target.elts
            if any(isinstance(e, ast.Starred) for e in names):
                for e in names:
                    self.assign(e.value if isinstance(e, ast.Starred) else e, _Val(list(v.atoms)), env, at)
                return
            if v.elts is not None and len(v.elts) != len(names):
                if v.origin:
                    step, n = v.origin
                    self.diag("unpack-mismatch", at, f"{step} produces {n} output{'s' if n != 1 else ''}, but {len(names)} names are unpacked", step=step)
                for e in names:
                    self.assign(e, _Val(list(v.atoms)), env, at)
                return
            if v.elts is None and v.origin and v.origin[1] == 1:
                step, _ = v.origin
                self.diag("unpack-mismatch", at, f"{step} produces 1 output, but {len(names)} names are unpacked", step=step)
            for i, e in enumerate(names):
                self.assign(e, v.elts[i] if v.elts is not None else _Val(list(v.atoms)), env, at)

    # ---- expressions
    def expr(self, e: ast.AST | None, env: dict[str, _Val]) -> _Val:
        if e is None:
            return _UNKNOWN
        if isinstance(e, ast.Name):
            return env.get(e.id, _UNKNOWN)
        if isinstance(e, ast.Call):
            return self.call(e, env)
        if isinstance(e, (ast.Tuple, ast.List)):
            vals = [self.expr(x.value if isinstance(x, ast.Starred) else x, env) for x in e.elts]
            return _Val(_Val.union(*vals).atoms, vals if isinstance(e, ast.Tuple) else None)
        if isinstance(e, ast.Subscript):
            v = self.expr(e.value, env)
            self.expr(e.slice, env)
            if v.elts is not None and isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, int) and -len(v.elts) <= e.slice.value < len(v.elts):
                return v.elts[e.slice.value]
            return _Val(list(v.atoms))
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            local = dict(env)
            for g in e.generators:
                it = self.expr(g.iter, local)
                self.assign(g.target, _Val(list(it.atoms)), local, e)
            self.ctx.append(("loop",))
            parts = [e.key, e.value] if isinstance(e, ast.DictComp) else [e.elt]
            vals = [self.expr(p, local) for p in parts]
            for g in e.generators:
                for cond in g.ifs:
                    self.expr(cond, local)
            self.ctx.pop()
            return _Val(_Val.union(*vals).atoms)
        if isinstance(e, ast.Lambda):
            return _UNKNOWN
        vals = [self.expr(c, env) for c in ast.iter_child_nodes(e) if isinstance(c, ast.expr)]
        return _Val(_Val.union(*vals).atoms) if vals else _UNKNOWN

    def callee(self, f: ast.AST) -> tuple[Any, str | None, bool, bool]:
        """(step, explicit node name, submitted, renamed) for a call's function, or (None, ...)."""
        submit = False
        if isinstance(f, ast.Attribute) and f.attr == "submit":
            submit, f = True, f.value
        if isinstance(f, ast.Call) and isinstance(f.func, ast.Attribute) and f.func.attr == "named":
            return self.lookup(f.func.value), None, submit, True  # one node name per key, by construction
        if isinstance(f, ast.Call) and isinstance(f.func, ast.Attribute) and f.func.attr == "with_options":
            step = self.lookup(f.func.value)
            name = next((k.value.value for k in f.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)), None)
            return step, name, submit, name is not None
        return self.lookup(f), None, submit, False

    def lookup(self, f: ast.AST) -> Any:
        if isinstance(f, ast.Name):
            obj = self.resolve(f.id)
            return obj if _is_step(obj) else None
        return None

    def call(self, e: ast.Call, env: dict[str, _Val]) -> _Val:
        f = e.func
        if isinstance(f, ast.Attribute) and f.attr in ("result", "wait") and not e.args:
            return self.expr(f.value, env)  # a future's value
        step, explicit, submit, renamed = self.callee(f)
        if step is not None:
            return self.step_call(e, step, explicit, submit, renamed, env)
        helper = self.resolve(f.id) if isinstance(f, ast.Name) else None
        args = [self.expr(a.value if isinstance(a, ast.Starred) else a, env) for a in e.args]
        kwargs = {k.arg: self.expr(k.value, env) for k in e.keywords}
        staged = getattr(helper, "__dendrite_stage__", None)
        if inspect.isfunction(helper) and helper.__module__ == self.module and (self.depth < 1 or (staged and self.depth < 3)):
            if staged:  # a `@dendrite.stage` function: its steps belong to that stage
                self.ctx.append(("stage", staged["name"]))
            try:
                expanded = self.expand(inspect.unwrap(helper) if staged else helper, e, args, kwargs)
            finally:
                if staged:
                    self.ctx.pop()
            if expanded is not None:
                return expanded
        flowing = _Val.union(*args, *kwargs.values())
        if not flowing.atoms or _incidental(f, self.resolve):
            return _UNKNOWN
        nid = f"dyn:{e.lineno}:{e.col_offset}"
        text = self.seg(e)
        self.nodes[nid] = {
            "id": nid, "kind": "dynamic", "name": text if len(text) <= 48 else text[:45] + "…", "source": text,
            "line": e.lineno, "conditional": self.conditions(), "repeated": self.in_loop(), "outputs": ["result"],
        }
        for a in flowing.atoms:
            self.edge(a, nid, "")
        return _Val([("out", nid, "result")])

    def expand(self, helper: Callable, e: ast.Call, args: list[_Val], kwargs: dict[str, _Val]) -> _Val | None:
        src = function_source(helper)
        func = _parse_function(src[0]) if src else None
        if func is None:
            return None
        try:
            bound = inspect.signature(helper).bind_partial(*args, **{k: v for k, v in kwargs.items() if k})
        except TypeError:
            return None
        inner = {k: v if isinstance(v, _Val) else _UNKNOWN for k, v in bound.arguments.items()}
        saved_source, self.source = self.source, src[0]
        self.depth += 1
        # Lines inside the helper refer to its own source; report them at the call site instead.
        before = len(self.diags)
        try:
            returned = self.run_body(func.body, inner)
        finally:
            self.depth -= 1
            self.source = saved_source
        for d in self.diags[before:]:
            d.message += f" (in {helper.__name__}, called here)"
            d.line, d.col = e.lineno, e.col_offset
        for n in self.nodes.values():
            if n.get("_helper_pending"):
                n["line"] = e.lineno
                del n["_helper_pending"]
        return _Val.union(*returned) if returned else _UNKNOWN

    def step_call(self, e: ast.Call, step: Any, explicit: str | None, submit: bool, renamed: bool, env: dict[str, _Val]) -> _Val:
        name = explicit or step.name
        self.steps[name] = step
        params = list(step.signature.parameters)
        bound: dict[str, _Val] = {}
        spread = any(isinstance(a, ast.Starred) for a in e.args) or any(k.arg is None for k in e.keywords)
        for i, a in enumerate(e.args):
            if isinstance(a, ast.Starred):
                self.expr(a.value, env)
                continue
            v = self.expr(a, env)
            if i < len(params):
                bound[params[i]] = v
            else:
                self.diag("unknown-arg", a, f"{name} takes {len(params)} argument{'s' if len(params) != 1 else ''}, but more are passed", step=name)
        for k in e.keywords:
            v = self.expr(k.value, env)
            if k.arg is None:
                continue
            if k.arg not in step.signature.parameters:
                self.diag("unknown-arg", k.value, f"{name} has no parameter {k.arg!r}" + _suggest(k.arg, params), step=name)
            else:
                bound[k.arg] = v
        if not spread:
            for p, spec in step.signature.parameters.items():
                if spec.default is inspect.Parameter.empty and p not in bound:
                    self.diag("missing-arg", e, f"{name} needs {p!r}, which is not passed and has no default", step=name)
        for arg, a in ((e.args[i], params[i]) for i in range(min(len(e.args), len(params)))):
            if isinstance(arg, ast.Name) and arg.id not in env and self.resolve(arg.id) is None and not hasattr(builtins, arg.id):
                self.diag("undefined-name", arg, f"{arg.id!r} is not defined here", step=name)
        for k in e.keywords:
            if isinstance(k.value, ast.Name) and k.value.id not in env and self.resolve(k.value.id) is None and not hasattr(builtins, k.value.id):
                self.diag("undefined-name", k.value, f"{k.value.id!r} is not defined here", step=name)

        path = tuple((f[1], f[2]) for f in self.ctx if f[0] == "if")
        earlier = self.calls.setdefault(name, [])
        if self.in_loop() and not renamed:
            self.diag("call-in-loop", e, f"{name} is called in a loop; each run may call a node once, so call {name}.named(key) with a key per item", step=name)
        elif not (renamed and explicit is None) and any(not _exclusive(path, p) for p in earlier):
            self.diag("duplicate-call", e, f"{name} is called more than once in the same run; give each call its own node with {name}.named(key)", step=name)
        earlier.append(path)

        node = self.nodes.get(name)
        if node is None:
            node = self.nodes[name] = {
                "id": name, "kind": "node", "name": name, "step": step.name, "line": e.lineno,
                "conditional": self.conditions(), "repeated": self.in_loop(), "submit": submit, "stage": self.stages(),
                "outputs": list(step.outputs), "params": [p for p in params if p not in bound],
            }
            if self.depth:
                node["_helper_pending"] = True
        else:
            # The same step in exclusive branches: one node at run time, under whichever conditions
            # both calls share (an if/else pair shares none, so the step always runs).
            shared = []
            for a, b in zip(node["conditional"], self.conditions()):
                if a != b:
                    break
                shared.append(a)
            node["conditional"] = shared
            node.setdefault("alternatives", 1)
            node["alternatives"] += 1
        for pname, v in bound.items():
            for a in v.atoms:
                self.edge(a, name, pname)
        outs = list(step.outputs)
        if len(outs) == 1:
            return _Val([("out", name, outs[0])], None, (name, 1))
        elts = [_Val([("out", name, o)]) for o in outs]
        return _Val([a for v in elts for a in v.atoms], elts, (name, len(outs)))


_LOGGERS = ("log", "logger", "logging", "warnings", "print")


def _incidental(f: ast.AST, resolve: Callable[[str], Any]) -> bool:
    """Calls that only look at a value (``print``, ``len``, logging), not steps in disguise."""
    if isinstance(f, ast.Name):
        return resolve(f.id) is None and hasattr(builtins, f.id)
    base = (_dotted(f) or "").split(".")[0]
    return base in _LOGGERS


def _exclusive(a: tuple, b: tuple) -> bool:
    """Two calls can't both run if, under some shared ``if``, they sit in different arms."""
    arms_a = dict(a)
    return any(i in arms_a and arms_a[i] != arm for i, arm in b)


def _suggest(name: str, options: list[str]) -> str:
    import difflib

    close = difflib.get_close_matches(name, options, n=1)
    return f"; did you mean {close[0]!r}?" if close else ""


def pipeline_shape(source: str, resolve: Callable[[str], Any], module: str | None = None) -> dict[str, Any]:
    """Static dataflow graph of a pipeline function's source (see the module docstring).

    ``resolve(name)`` returns the module-level object a name refers to (or None); steps are
    recognised from it, and same-module helper functions are expanded one level.
    """
    source = textwrap.dedent(source)
    try:
        mod = ast.parse(source)
    except SyntaxError as e:
        d = Diagnostic("syntax", "error", e.msg or "invalid syntax", line=e.lineno, col=(e.offset or 1) - 1)
        return {"nodes": [], "edges": [], "diagnostics": [d.as_dict()], "dynamic": False, "_steps": {}}
    func = next((n for n in mod.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if func is None:
        d = Diagnostic("syntax", "error", "expected a pipeline function definition", line=1, col=0)
        return {"nodes": [], "edges": [], "diagnostics": [d.as_dict()], "dynamic": False, "_steps": {}}
    b = _ShapeBuilder(source, resolve, module)
    env: dict[str, _Val] = {}
    inputs = []
    for a in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]:
        env[a.arg] = _Val([("input", a.arg)])
        inputs.append({"id": f"input:{a.arg}", "kind": "input", "name": a.arg, "line": func.lineno})
    b.run_body(func.body, env)
    used = {e["from"] for e in b.edges}
    nodes = [n for n in inputs if n["id"] in used or not b.nodes] + list(b.nodes.values())
    return {
        "name": func.name,
        "nodes": nodes,
        "edges": b.edges,
        "diagnostics": [d.as_dict() for d in b.diags],
        "dynamic": any(n["kind"] == "dynamic" for n in b.nodes.values()),
        "_steps": b.steps,
    }
