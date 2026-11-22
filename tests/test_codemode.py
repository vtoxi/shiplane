"""The Code Mode interpreter (coderunner/codemode.py): the Python subset works, and nothing outside it is reachable.

    python tests/test_codemode.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fakes import run_tests  # noqa: E402
from coderunner import codemode as C  # noqa: E402


def run(code, **kw):
    return C.run(code, **kw)[0]


def fails(code, text, limit=False, **kw):
    try:
        C.run(code, **kw)
    except C.CodeError as e:
        assert text in str(e), (code, str(e))
        assert isinstance(e, C.Limit) == limit, (code, type(e))
        return str(e)
    raise AssertionError('should have failed: %r' % code)


def test_result_last_expression_and_print():
    assert C.run('x = 2\nx * 3') == (6, '')
    assert C.run('result = 5\n7') == (5, '')
    assert C.run('x = 1') == (None, '')
    assert C.run('print("a", 1, sep="-")\nprint("b", end="")') == (None, 'a-1\nb')


def test_data_and_operators():
    assert run('[1, *[2, 3]] + [4]') == [1, 2, 3, 4]
    assert run('(1, 2)') == (1, 2) and run('{1, 2} | {3}') == {1, 2, 3}
    assert run('{"a": 1, **{"b": 2}}') == {'a': 1, 'b': 2}
    assert run('[7 // 2, 7 % 2, 2 ** 3, 1 << 2, 8 >> 1, 6 & 3, 6 ^ 3, 7 / 2, 5 - 1]') == [3, 1, 8, 4, 4, 2, 5, 3.5, 4]
    assert run('[-1, +1, ~0, not 0]') == [-1, 1, -1, True]
    assert run('[1 < 2 < 3, 1 < 2 > 5, 1 in [1], 2 not in [1], None is None, 1 is not None, 1 == 1, 1 != 2, 2 >= 2, 1 <= 0]') == \
        [True, False, True, True, True, True, True, True, True, False]
    assert run('[0 or "x", 1 and 2, 0 and 1, "" or 0]') == ['x', 2, 0, 0]
    assert run('"y" if 0 else "n"') == 'n'
    assert run('"abcdef"[1:4]') == 'bcd' and run('[1, 2, 3][::-1]') == [3, 2, 1] and run('{"k": [5]}["k"][0]') == 5


def test_statements():
    code = '''
total = 0
for i in range(10):
    if i == 2:
        continue
    if i == 5:
        break
    total += i
else:
    total = -1
n = 0
while n < 3:
    n += 1
else:
    n += 10
while True:
    break
for x in []:
    pass
else:
    flag = 1
d = {"a": [1]}
d["a"] += [2]
d["b"] = 3
a, (b, c) = 1, [2, 3]
del d["b"]
z = 1
del z
result = [total, n, flag, d, a, b, c]
'''
    assert run(code) == [8, 13, 1, {'a': [1, 2]}, 1, 2, 3]


def test_functions_lambdas_comprehensions():
    code = '''
def add(a, b=10):
    return a + b
def nothing():
    pass
def early(x):
    if x:
        return "yes"
    return
k = 3
f = lambda x: x * k
result = [add(1), add(1, 2), add(b=1, a=2), nothing(), early(1), early(0), f(2),
          [x * y for x in range(3) if x for y in [1, 10]], {x: x * x for x in range(3)}, {x % 2 for x in range(5)},
          sum(x for x in range(4)), sorted([3, 1, 2], key=lambda v: -v), list(map(lambda v: v + 1, [1, 2])),
          list(filter(None, [0, 1, 2])), list(filter(lambda v: v > 1, [1, 2, 3])), list(reversed([1, 2])),
          list(enumerate("ab", 1)), list(zip([1, 2], "ab")), min(3, 1), max([1, 5]), abs(-2), round(2.6), any([0, 1]), all([1, 0]),
          str(1), int("2"), float("1.5"), bool(0), tuple([1]), dict(a=1), set([1, 1]), len("abc")]
'''
    assert run(code) == [11, 3, 3, None, 'yes', None, 6, [1, 10, 2, 20], {0: 0, 1: 1, 2: 4}, {0, 1}, 6, [3, 2, 1], [2, 3], [1, 2], [2, 3], [2, 1],
                         [(1, 'a'), (2, 'b')], [(1, 'a'), (2, 'b')], 1, 5, 2, 3, True, False, '1', 2, 1.5, False, (1,), {'a': 1}, {1}, 3]
    fails('def f(a): return a\nf(1, 2)', 'takes 1 arguments')
    fails('def f(a): return a\nf(b=1)', 'unexpected or repeated')
    fails('def f(a): return a\nf()', "missing argument 'a'")
    fails('def f(*a): pass', '*args')
    fails('@print\ndef f(): pass', 'decorators')


def test_methods_and_fstrings():
    code = '''
s = " Hello World "
l = [3, 1]
l.append(2)
l.sort()
d = {"a": 1}
d.update({"b": 2})
st = {1}
st.add(2)
result = [s.strip().lower(), s.split(), "-".join(["a", "b"]), "aXa".replace("X", "yy"), l, d.get("z", 0), sorted(d.keys()), list(d.items()),
          st, f"{1 + 1}!", f"{'x'!r:>4}", f"{3.14159:.2f}", f"{'s'!s}{'a'!a}"]
'''
    assert run(code) == ['hello world', ['Hello', 'World'], 'a-b', 'ayya', [1, 2, 3], 0, ['a', 'b'], [('a', 1), ('b', 2)], {1, 2}, '2!', " 'x'", '3.14', "s'a'"]


def test_try_except_finally():
    code = '''
out = []
try:
    {}["k"]
except ValueError:
    out.append("wrong")
except (TypeError, KeyError) as e:
    out.append("key " + e)
try:
    1 / 0
except Exception as e:
    out.append(e)
else:
    out.append("not here")
finally:
    out.append("fin")
try:
    x = 1
except:
    pass
else:
    out.append("else")
try:
    boom()
except:
    out.append("bare")
result = out
'''
    assert run(code, funcs={'boom': lambda: 1 / 0}) == ["key 'k'", 'division by zero', 'fin', 'else', 'bare']
    fails('try:\n    {}["k"]\nexcept ValueError:\n    pass', 'KeyError')


def test_funcs_and_values_from_the_caller():
    calls = []
    assert run('tool("a", {"x": 1}, y=2)', funcs={'tool': lambda n, a=None, **kw: calls.append((n, a, kw)) or 'ok'}) == 'ok'
    assert calls == [('a', {'x': 1}, {'y': 2})]
    assert run('[t["n"] for t in tools]', values={'tools': [{'n': 1}]}) == [1]


def test_nothing_outside_the_subset_is_reachable():
    for code, text in (('import os', 'Import is not supported'), ('from os import path', 'ImportFrom'), ('class A: pass', 'ClassDef'),
                       ('with x: pass', 'With'), ('global x', 'Global'), ('"".__class__', "no attribute '__class__'"),
                       ('[].__class__.__mro__', 'no attribute'), ('(lambda: 1).__globals__', 'no attribute'), ('len.__self__', 'no attribute'),
                       ('{}.get.__self__', 'no attribute'), ('"{0.__class__}".format(1)', "no attribute 'format'"), ('open("/etc/passwd")', 'NameError'),
                       ('__import__("os")', 'NameError'), ('eval("1")', 'NameError'), ('getattr(1, "real")', 'NameError'), ('type(1)', 'NameError'),
                       ('x = 1\nx()', 'not callable'), ('"%s" % 1', '%-formatting'), ('1 @ 2', 'this operator'), ('async def f(): pass', 'AsyncFunctionDef'),
                       ('x.y = 1', 'assignment target'), ('"abc"[0] = 1', 'only lists and dicts'), ('del x.y', 'del target'), ('del nope', 'NameError'),
                       ('a.b += 1', 'assignment target'), ('a, b = [1]', 'cannot unpack'), ('nope', 'NameError'), ('(yield)', 'Yield'),
                       ('break', 'outside a loop'), ('return 1', 'return outside'), ('if 1 1', 'syntax error'), ('', 'empty'),
                       ('x' * (C.MAX_CODE + 1), 'too long'), ('[x async for x in y]', 'async')):
        fails(code, text)


def test_limits():
    fails('while True:\n    pass', 'too many steps', limit=True)
    fails('"a" * 10 ** 9', 'too large', limit=True)
    fails('[0] * 10 ** 9', 'too large', limit=True)
    fails('2 ** 100000', 'exponent', limit=True)
    fails('1 << 100000', 'shift', limit=True)
    fails('range(10 ** 9)', 'range too large', limit=True)
    fails('f"{1:99999}"', 'format width', limit=True)
    fails('def f(n): return f(n + 1)\nf(0)', 'nested too deeply', limit=True)
    fails('s = "a" * 900000\ns + s', 'too large', limit=True)
    fails('s = "a" * 900000\ns.replace("a", "aa")', 'too large', limit=True)
    fails('"x" * 2000 + "".join(["y"] * 1000)\n"," .join(["a"] * 999999)', 'too large', limit=True)
    fails('l = [0] * 900000\nl.extend(l)', 'too large', limit=True)
    fails('[1, *range(999999), 2, 3]', 'too large', limit=True)
    fails('x = [[0] * 900000 for i in range(10)]', 'too much data', limit=True)
    fails('sum(["a"], "")', 'start must be a number')
    fails('x = 0\nwhile True:\n    x += 1', 'time limit', limit=True, seconds=0)
    for i in range(3):
        fails('try:\n    while True:\n        pass\nexcept:\n    pass', 'too many steps', limit=True)     # a limit cannot be caught
    assert C.run('print("x" * 40000)\nprint("y" * 40000)')[1] == 'x' * 40000 + '\n' + 'y' * (C.MAX_PRINT - 40001)


def test_reprs_and_deep_nesting():
    assert repr(run('[(lambda: 1), "a".upper]')) == '[<function <lambda>>, <method upper>]'
    fails('(' * 500 + '1' + ')' * 500, '')                                       # too deep for the parser or the walker: an error, not a crash


if __name__ == '__main__':
    run_tests(globals())
