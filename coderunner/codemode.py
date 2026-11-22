"""Code Mode: runs a short snippet written in a safe subset of Python, for the MCP tools shiplane_search and shiplane_execute (mcp_core.py).

Instead of reading ~100 tool definitions and calling them one by one, the model writes a few lines that find, call, chain and filter, and only the final
value comes back. Nothing here uses exec/eval: the snippet is parsed with `ast` and walked node by node. Reachable are plain data (str, numbers, bool,
None, list, dict, set, tuple), functions the snippet defines, an allow-list of builtins and methods (no name starts with _), and the functions the caller
passes in. No imports, classes, with, global, yield or async. Every node costs a step, everything built costs cells, wall time is capped and big
allocations are refused before they happen, so a bad snippet ends with an error instead of hurting the server. Python standard library only.

    value, printed = run(code, funcs={'tool': fn}, values={'tools': [...]}, seconds=60)
"""
import ast
import operator
import re
import time

MAX_CODE = 20000          # characters of source
MAX_STEPS = 200000        # nodes evaluated
MAX_CELLS = 5000000       # characters + items built, in total
MAX_LEN = 1000000         # biggest string / list a single operation may build
MAX_DEPTH = 20            # nested calls of snippet functions
MAX_PRINT = 50000         # characters of print() output kept


class CodeError(Exception):
    """The snippet failed (syntax, an error while running, a limit). The message names the line."""


class Limit(CodeError):
    """A limit was hit. Snippet code cannot catch it."""


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    def __init__(self, value):
        Exception.__init__(self)
        self.value = value


NOCATCH = (CodeError, _Break, _Continue, _Return, RecursionError, MemoryError)

METHODS = {
    str: {'lower', 'upper', 'title', 'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'splitlines', 'join', 'startswith', 'endswith', 'replace', 'find', 'count',
          'isdigit'},
    list: {'append', 'extend', 'insert', 'pop', 'remove', 'index', 'count', 'sort', 'reverse', 'copy'},
    dict: {'get', 'keys', 'values', 'items', 'pop', 'setdefault', 'update', 'copy'},
    set: {'add', 'discard', 'remove', 'union', 'intersection', 'difference', 'copy'},
    tuple: {'index', 'count'},
}

BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
          ast.Mod: operator.mod, ast.Pow: operator.pow, ast.LShift: operator.lshift, ast.RShift: operator.rshift, ast.BitOr: operator.or_,
          ast.BitAnd: operator.and_, ast.BitXor: operator.xor}
CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
          ast.Is: operator.is_, ast.IsNot: operator.is_not, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
UNOPS = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}
SIZED = (str, list, tuple, dict, set)


def too_big(n, what='result'):
    if n > MAX_LEN:
        raise Limit('%s too large (over %d items)' % (what, MAX_LEN))


class Scope:
    __slots__ = ('vars', 'parent')

    def __init__(self, parent=None):
        self.vars, self.parent = {}, parent

    def find(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s
            s = s.parent
        return None


class Function:
    """A def or lambda from the snippet; calling it runs its body in the interpreter."""

    def __init__(self, interp, name, node, scope):
        a = node.args
        if a.vararg or a.kwarg or a.kwonlyargs or getattr(a, 'posonlyargs', None):
            raise interp.unsupported('*args, **kwargs and keyword-only arguments')
        self.interp, self.name, self.node, self.scope = interp, name, node, scope
        self.params = [x.arg for x in a.args]
        self.defaults = [interp.eval(d, scope) for d in a.defaults]

    def __call__(self, *args, **kw):
        return self.interp.call_function(self, args, kw)

    def __repr__(self):
        return '<function %s>' % self.name


class Method:
    """An allow-listed method of a plain value, with size checks before anything big is built."""

    def __init__(self, obj, name):
        self.obj, self.name = obj, name

    def __call__(self, *a, **kw):
        o, n = self.obj, self.name
        if n == 'replace' and len(a) >= 2 and isinstance(a[0], str) and isinstance(a[1], str):
            hits = o.count(a[0]) if a[0] else len(o) + 1
            too_big(len(o) + hits * max(0, len(a[1]) - len(a[0])))
        elif n == 'join' and a:
            items = list(a[0])
            too_big(len(o) * max(0, len(items) - 1) + sum(len(x) for x in items if isinstance(x, str)))
            a = (items,) + a[1:]
        elif n in ('extend', 'update') and a and isinstance(a[0], SIZED):
            too_big(len(o) + len(a[0]))
        return getattr(o, n)(*a, **kw)

    def __repr__(self):
        return '<method %s>' % self.name


class Interp:
    def __init__(self, funcs, values, seconds):
        self.funcs = self.builtins()
        self.funcs.update(funcs or {})
        self.allowed = list(self.funcs.values())
        self.root = Scope()
        self.root.vars.update(values or {})
        self.steps = self.cells = self.depth = self.line = 0
        self.deadline = time.monotonic() + seconds
        self.printed, self.plen = [], 0

    # ---------------------------------------------------------------- bookkeeping
    def step(self, node):
        self.line = getattr(node, 'lineno', self.line)
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise Limit('line %d: too many steps (over %d): simplify the snippet' % (self.line, MAX_STEPS))
        if time.monotonic() > self.deadline:
            raise Limit('line %d: time limit reached' % self.line)

    def charge(self, v):
        if isinstance(v, SIZED):
            self.cells += len(v)
            if self.cells > MAX_CELLS:
                raise Limit('line %d: the snippet built too much data (over %d items)' % (self.line, MAX_CELLS))
        return v

    def unsupported(self, what):
        return CodeError('line %d: %s is not supported in Code Mode' % (self.line, what))

    # ---------------------------------------------------------------- builtins
    def builtins(self):
        def _range(*a):
            r = range(*a)
            too_big(len(r), 'range')
            return r

        def _sum(xs, start=0):
            if not isinstance(start, (int, float)):
                raise TypeError('sum() start must be a number')
            return sum(xs, start)

        def _print(*a, sep=' ', end='\n'):
            text = str(sep).join(str(x) for x in a) + str(end)
            if self.plen < MAX_PRINT:
                self.printed.append(text[:MAX_PRINT - self.plen])
            self.plen += len(text)

        return {'len': len, 'range': _range, 'min': min, 'max': max, 'sum': _sum, 'sorted': sorted, 'any': any, 'all': all, 'abs': abs, 'round': round,
                'str': str, 'int': int, 'float': float, 'bool': bool, 'list': list, 'dict': dict, 'set': set, 'tuple': tuple, 'print': _print,
                'reversed': lambda xs: list(reversed(list(xs))), 'enumerate': lambda xs, start=0: list(enumerate(xs, start)),
                'zip': lambda *xs: list(zip(*xs)), 'map': lambda f, xs: [f(x) for x in xs],
                'filter': lambda f, xs: [x for x in xs if (f(x) if f is not None else x)]}

    # ---------------------------------------------------------------- statements
    def block(self, stmts, scope):
        for st in stmts:
            self.exec(st, scope)

    def exec(self, node, scope):
        self.step(node)
        fn = getattr(self, 's_' + type(node).__name__, None)
        if fn is None:
            raise self.unsupported(type(node).__name__)
        fn(node, scope)

    def s_Expr(self, node, scope):
        self.eval(node.value, scope)

    def s_Pass(self, node, scope):
        pass

    def s_Break(self, node, scope):
        raise _Break()

    def s_Continue(self, node, scope):
        raise _Continue()

    def s_Return(self, node, scope):
        raise _Return(self.eval(node.value, scope) if node.value is not None else None)

    def s_Assign(self, node, scope):
        v = self.eval(node.value, scope)
        for t in node.targets:
            self.assign(t, v, scope)

    def s_AugAssign(self, node, scope):
        t = node.target
        if isinstance(t, ast.Name):
            self.assign(t, self.binop(node.op, self.eval(t, scope), self.eval(node.value, scope)), scope)
        elif isinstance(t, ast.Subscript):
            obj, key = self.eval(t.value, scope), self.eval(t.slice, scope)
            obj[key] = self.binop(node.op, obj[key], self.eval(node.value, scope))
        else:
            raise self.unsupported('this assignment target')

    def assign(self, t, v, scope):
        if isinstance(t, ast.Name):
            scope.vars[t.id] = v
        elif isinstance(t, (ast.Tuple, ast.List)):
            items = list(v)
            if len(items) != len(t.elts):
                raise ValueError('cannot unpack %d values into %d names' % (len(items), len(t.elts)))
            for sub, item in zip(t.elts, items):
                self.assign(sub, item, scope)
        elif isinstance(t, ast.Subscript):
            obj = self.eval(t.value, scope)
            if type(obj) not in (list, dict):
                raise TypeError('only lists and dicts can be changed by index')
            obj[self.eval(t.slice, scope)] = v
        else:
            raise self.unsupported('this assignment target')

    def s_Delete(self, node, scope):
        for t in node.targets:
            if isinstance(t, ast.Name):
                s = scope.find(t.id)
                if s is None:
                    raise NameError("name '%s' is not defined" % t.id)
                del s.vars[t.id]
            elif isinstance(t, ast.Subscript):
                del self.eval(t.value, scope)[self.eval(t.slice, scope)]
            else:
                raise self.unsupported('this del target')

    def s_If(self, node, scope):
        self.block(node.body if self.eval(node.test, scope) else node.orelse, scope)

    def loop_body(self, node, scope):
        """Run one pass of a loop body; True means break."""
        try:
            self.block(node.body, scope)
        except _Break:
            return True
        except _Continue:
            pass
        return False

    def s_For(self, node, scope):
        for v in self.eval(node.iter, scope):
            self.assign(node.target, v, scope)
            if self.loop_body(node, scope):
                return
        self.block(node.orelse, scope)

    def s_While(self, node, scope):
        while self.eval(node.test, scope):
            if self.loop_body(node, scope):
                return
        self.block(node.orelse, scope)

    def s_FunctionDef(self, node, scope):
        if node.decorator_list:
            raise self.unsupported('decorators')
        scope.vars[node.name] = Function(self, node.name, node, scope)

    def s_Try(self, node, scope):
        try:
            try:
                self.block(node.body, scope)
            except NOCATCH:
                raise
            except Exception as e:
                names = {c.__name__ for c in type(e).__mro__}
                for h in node.handlers:
                    want = [] if h.type is None else [x.id for x in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]) if isinstance(x, ast.Name)]
                    if not want or names.intersection(want):
                        if h.name:
                            scope.vars[h.name] = str(e) or type(e).__name__        # the message, as a string
                        self.block(h.body, scope)
                        break
                else:
                    raise
            else:
                self.block(node.orelse, scope)
        finally:
            self.block(node.finalbody, scope)

    # ---------------------------------------------------------------- expressions
    def eval(self, node, scope):
        self.step(node)
        fn = getattr(self, 'e_' + type(node).__name__, None)
        if fn is None:
            raise self.unsupported(type(node).__name__)
        return fn(node, scope)

    def e_Constant(self, node, scope):
        return node.value

    def e_Index(self, node, scope):              # Python 3.8 wraps subscripts in ast.Index
        return self.eval(node.value, scope)

    def e_Name(self, node, scope):
        s = scope.find(node.id)
        if s is not None:
            return s.vars[node.id]
        if node.id in self.funcs:
            return self.funcs[node.id]
        raise NameError("name '%s' is not defined" % node.id)

    def items(self, elts, scope):
        out = []
        for e in elts:
            if isinstance(e, ast.Starred):
                out.extend(self.eval(e.value, scope))
            else:
                out.append(self.eval(e, scope))
            too_big(len(out))
        return out

    def e_List(self, node, scope):
        return self.charge(self.items(node.elts, scope))

    def e_Tuple(self, node, scope):
        return self.charge(tuple(self.items(node.elts, scope)))

    def e_Set(self, node, scope):
        return self.charge(set(self.items(node.elts, scope)))

    def e_Dict(self, node, scope):
        d = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                d.update(self.eval(v, scope))
            else:
                d[self.eval(k, scope)] = self.eval(v, scope)
        return self.charge(d)

    def binop(self, op, a, b):
        kind = type(op)
        if kind not in BINOPS:
            raise self.unsupported('this operator')
        if kind is ast.Mult:
            for seq, n in ((a, b), (b, a)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int):
                    too_big(len(seq) * n)
        elif kind is ast.Add and isinstance(a, (str, list, tuple)) and isinstance(b, (str, list, tuple)):
            too_big(len(a) + len(b))
        elif kind is ast.Pow and isinstance(a, int) and isinstance(b, int) and abs(a) > 1 and b > 10000:
            raise Limit('line %d: exponent too large' % self.line)
        elif kind is ast.LShift and isinstance(b, int) and b > 10000:
            raise Limit('line %d: shift too large' % self.line)
        elif kind is ast.Mod and isinstance(a, str):
            raise self.unsupported('%-formatting (use an f-string)')
        return self.charge(BINOPS[kind](a, b))

    def e_BinOp(self, node, scope):
        return self.binop(node.op, self.eval(node.left, scope), self.eval(node.right, scope))

    def e_UnaryOp(self, node, scope):
        return UNOPS[type(node.op)](self.eval(node.operand, scope))

    def e_BoolOp(self, node, scope):
        is_and = isinstance(node.op, ast.And)
        v = None
        for x in node.values:
            v = self.eval(x, scope)
            if bool(v) != is_and:
                return v
        return v

    def e_Compare(self, node, scope):
        left = self.eval(node.left, scope)
        for op, comp in zip(node.ops, node.comparators):
            right = self.eval(comp, scope)
            if not CMPOPS[type(op)](left, right):
                return False
            left = right
        return True

    def e_IfExp(self, node, scope):
        return self.eval(node.body if self.eval(node.test, scope) else node.orelse, scope)

    def e_Attribute(self, node, scope):
        obj = self.eval(node.value, scope)
        if node.attr in METHODS.get(type(obj), ()):
            return Method(obj, node.attr)
        raise AttributeError("'%s' has no attribute '%s' in Code Mode" % (type(obj).__name__, node.attr))

    def e_Subscript(self, node, scope):
        return self.eval(node.value, scope)[self.eval(node.slice, scope)]

    def e_Slice(self, node, scope):
        return slice(*[self.eval(x, scope) if x is not None else None for x in (node.lower, node.upper, node.step)])

    def e_Call(self, node, scope):
        f = self.eval(node.func, scope)
        args = self.items(node.args, scope)
        kw = {}
        for k in node.keywords:
            if k.arg is None:
                kw.update(self.eval(k.value, scope))
            else:
                kw[k.arg] = self.eval(k.value, scope)
        if not (isinstance(f, (Function, Method)) or any(f is g for g in self.allowed)):
            raise TypeError("'%s' object is not callable" % type(f).__name__)
        return self.charge(f(*args, **kw))

    def call_function(self, f, args, kw):
        self.depth += 1
        try:
            if self.depth > MAX_DEPTH:
                raise Limit('line %d: functions nested too deeply (over %d)' % (self.line, MAX_DEPTH))
            s = Scope(f.scope)
            if len(args) > len(f.params):
                raise TypeError('%s() takes %d arguments but %d were given' % (f.name, len(f.params), len(args)))
            s.vars.update(zip(f.params, args))
            for k, v in kw.items():
                if k not in f.params or k in s.vars:
                    raise TypeError('%s() got an unexpected or repeated argument %r' % (f.name, k))
                s.vars[k] = v
            first_default = len(f.params) - len(f.defaults)
            for i, p in enumerate(f.params):
                if p not in s.vars:
                    if i < first_default:
                        raise TypeError('%s() missing argument %r' % (f.name, p))
                    s.vars[p] = f.defaults[i - first_default]
            if isinstance(f.node, ast.Lambda):
                return self.eval(f.node.body, s)
            try:
                self.block(f.node.body, s)
            except _Return as r:
                return r.value
            return None
        finally:
            self.depth -= 1

    def e_Lambda(self, node, scope):
        return Function(self, '<lambda>', node, scope)

    def comprehension(self, node, scope, emit):
        inner = Scope(scope)

        def loop(i):
            if i == len(node.generators):
                emit(inner)
                return
            g = node.generators[i]
            if getattr(g, 'is_async', 0):
                raise self.unsupported('async comprehensions')
            for v in self.eval(g.iter, inner):
                self.assign(g.target, v, inner)
                if all(self.eval(c, inner) for c in g.ifs):
                    loop(i + 1)
        loop(0)

    def e_ListComp(self, node, scope):
        out = []
        self.comprehension(node, scope, lambda s: out.append(self.charge(self.eval(node.elt, s))))
        return self.charge(out)

    e_GeneratorExp = e_ListComp                  # generators are built as lists

    def e_SetComp(self, node, scope):
        return self.charge(set(self.e_ListComp(node, scope)))

    def e_DictComp(self, node, scope):
        out = {}

        def put(s):
            out[self.eval(node.key, s)] = self.eval(node.value, s)
        self.comprehension(node, scope, put)
        return self.charge(out)

    def e_JoinedStr(self, node, scope):
        return self.charge(''.join(str(self.eval(v, scope)) for v in node.values))

    def e_FormattedValue(self, node, scope):
        v = self.eval(node.value, scope)
        v = {115: str, 114: repr, 97: ascii}.get(node.conversion, lambda x: x)(v)
        spec = self.eval(node.format_spec, scope) if node.format_spec is not None else ''
        if re.search(r'\d{4,}', spec):
            raise Limit('line %d: format width too large' % self.line)
        return format(v, spec)


def run(code, funcs=None, values=None, seconds=60):
    """Run a snippet. Returns (value, printed text): value is the variable `result` when the snippet sets it, else the value of its last expression line."""
    if not isinstance(code, str) or not code.strip():
        raise CodeError('code is empty')
    if len(code) > MAX_CODE:
        raise CodeError('code too long (over %d characters)' % MAX_CODE)
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise CodeError('syntax error on line %s: %s' % (e.lineno, e.msg))
    it = Interp(funcs, values, seconds)
    last = None
    try:
        for st in tree.body:
            last = None
            if isinstance(st, ast.Expr):
                it.step(st)
                last = it.eval(st.value, it.root)
            else:
                it.exec(st, it.root)
    except CodeError as e:
        if str(e).startswith(('line ', 'syntax')):
            raise
        raise type(e)('line %d: %s' % (it.line, e))
    except (_Break, _Continue):
        raise CodeError('line %d: break/continue outside a loop' % it.line)
    except _Return:
        raise CodeError('line %d: return outside a function (set result = ... instead)' % it.line)
    except RecursionError:
        raise Limit('line %d: nested too deeply' % it.line)
    except Exception as e:
        raise CodeError('line %d: %s: %s' % (it.line, type(e).__name__, e))
    value = it.root.vars['result'] if 'result' in it.root.vars else last
    return value, ''.join(it.printed)
