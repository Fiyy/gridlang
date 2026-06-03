"""Run the test suite without pytest (the env lacks _ssl, breaking pytest imports)."""
import sys, unittest, glob, importlib.util, inspect, tempfile, shutil, re
from pathlib import Path
import pandas as pd


class _Approx:
    def __init__(self, expected, rel=None, abs=None):
        self.expected = expected
        self.rel = rel
        self.abs = abs
    def __eq__(self, other):
        try:
            tol = self.abs if self.abs is not None else (
                self.rel * abs(self.expected) if self.rel else 1e-7)
            return abs(other - self.expected) <= tol
        except Exception:
            return False
    def __repr__(self):
        return f'approx({self.expected})'


class _Raises:
    def __init__(self, exc, **kw):
        self.exc = exc
        self.match = kw.get('match')
    def __enter__(self):
        return self
    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError(f'expected {self.exc} not raised')
        if not issubclass(et, self.exc):
            return False
        if self.match and not re.search(self.match, str(ev)):
            raise AssertionError(f'match {self.match!r} not in {ev!r}')
        return True


class _PytestStub:
    @staticmethod
    def fixture(*a, **kw):
        if a and callable(a[0]):
            return a[0]
        def deco(fn): return fn
        return deco
    @classmethod
    def raises(cls, exc, **kw):
        return _Raises(exc, **kw)
    @staticmethod
    def approx(v, **kw):
        return _Approx(v, **kw)
    @staticmethod
    def skip(msg):
        raise unittest.SkipTest(msg)


sys.modules['pytest'] = _PytestStub

stats = {'passed': 0, 'failed': 0, 'skipped': 0}


def call_test(method, name_for_log, fixtures):
    sig = inspect.signature(method)
    kwargs = {}
    cleanup_dirs = []
    for pname in sig.parameters:
        if pname == 'self':
            continue
        if pname in fixtures:
            kwargs[pname] = fixtures[pname]
        elif pname == 'tmp_path':
            d = tempfile.mkdtemp()
            cleanup_dirs.append(d)
            kwargs[pname] = Path(d)
    try:
        method(**kwargs)
        stats['passed'] += 1
    except unittest.SkipTest:
        stats['skipped'] += 1
    except Exception as e:
        stats['failed'] += 1
        print(f'FAIL  {name_for_log}: {type(e).__name__}: {e}')
    finally:
        for d in cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


def main():
    files = sorted(glob.glob('tests/test_*.py'))
    for path in files:
        spec = importlib.util.spec_from_file_location(path, path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f'IMPORT FAIL {path}: {e}')
            stats['failed'] += 1
            continue

        # Detect fixtures: either zero-arg, or fixtures that take only tmp_path (a per-fixture tmpdir).
        fixtures = {}
        for name in dir(mod):
            if name.startswith('_') or name.startswith('Test'):
                continue
            obj = getattr(mod, name)
            if not callable(obj) or inspect.isclass(obj):
                continue
            try:
                sig = inspect.signature(obj)
            except (ValueError, TypeError):
                continue
            params = list(sig.parameters)
            try:
                if not params:
                    fixtures[name] = obj()
                elif params == ['tmp_path']:
                    d = tempfile.mkdtemp()
                    fixtures[name] = obj(Path(d))
            except Exception:
                pass

        for name in dir(mod):
            if not name.startswith('test_'):
                continue
            obj = getattr(mod, name)
            if not callable(obj) or inspect.isclass(obj):
                continue
            call_test(obj, f'{path}::{name}', fixtures)

        for name in dir(mod):
            cls = getattr(mod, name)
            if not (isinstance(cls, type) and name.startswith('Test')):
                continue
            for tn in dir(cls):
                if not tn.startswith('test_'):
                    continue
                inst = cls()
                # Support pytest-style xUnit hooks.
                setup = getattr(inst, 'setup_method', None)
                teardown = getattr(inst, 'teardown_method', None)
                if setup:
                    try:
                        sig = inspect.signature(setup)
                        if 'method' in sig.parameters:
                            setup(method=getattr(inst, tn))
                        else:
                            setup()
                    except Exception as e:
                        stats['failed'] += 1
                        print(f'FAIL  {path}::{name}::{tn} (setup): {type(e).__name__}: {e}')
                        continue
                method = getattr(inst, tn)
                call_test(method, f'{path}::{name}::{tn}', fixtures)
                if teardown:
                    try:
                        sig = inspect.signature(teardown)
                        if 'method' in sig.parameters:
                            teardown(method=getattr(inst, tn))
                        else:
                            teardown()
                    except Exception:
                        pass  # teardown failure shouldn't cascade

    print(f"\n{stats['passed']} passed, {stats['failed']} failed, {stats['skipped']} skipped")
    sys.exit(1 if stats['failed'] else 0)


if __name__ == '__main__':
    main()
