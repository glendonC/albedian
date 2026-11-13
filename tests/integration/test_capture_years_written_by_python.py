"""Capture times are written with Python's own four-digit years (unit capture-time-3).

What protects a capture time's year is the four-digit test at the end of this file, run on Linux under Python 3.12,
where the platform's formatting writes year 1 as "1" (3.12 hands strftime's year to the C library; 3.14 and macOS pad
it, so on a Mac that test passes either way). Before it, a cheap early warning that works on a Mac: a guard over the
three modules that write a capture time.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import inspect
from pathlib import Path
import string

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARDED = ("services/contracts/ingest.py", "services/ingest/metadata.py", "services/media/discovery.py")
# The locale's own date and time formats, which hold a year code the platform writes
LOCALE_FORMATS = frozenset({"nl_langinfo", "D_T_FMT", "D_FMT", "T_FMT", "T_FMT_AMPM", "ERA_D_T_FMT", "ERA_D_FMT", "ERA_T_FMT"})


def offences(source: str) -> list[tuple[int, str]]:
    """The guard, by reading the syntax. The forms it refuses are the rows of REFUSED_FORMS below, each planted in each
    guarded module and refused there by an assertion, and it claims no other: every other way to write a year (a
    format built at run time, a method fetched one line and called the next, a helper in another module among them)
    it does not see, and what protects the year is the four-digit test at the end of this file, on Linux under
    Python 3.12."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (node.attr == "strftime" or node.attr in LOCALE_FORMATS):
            found.append((node.lineno, f"the attribute {node.attr}"))
        elif isinstance(node, ast.Name) and (node.id == "strftime" or node.id in LOCALE_FORMATS):
            found.append((node.lineno, f"the name {node.id}"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    found.append((node.lineno, "a star import"))
                elif alias.name.rsplit(".", 1)[-1] in {"strftime", *LOCALE_FORMATS}:
                    found.append((node.lineno, f"an import of {alias.name}"))
        elif isinstance(node, ast.Call) and _fetched_by_a_name_not_a_literal(node.func):
            found.append((node.lineno, "a method fetched by a name that is not a literal"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and "strftime" in node.value:
            found.append((node.lineno, "a string naming strftime"))
        elif isinstance(node, ast.FormattedValue) and node.format_spec is not None and any(
                isinstance(part, ast.Constant) and "%" in str(part.value) for part in ast.walk(node.format_spec)):
            found.append((node.lineno, "an f-string's % format spec"))
        elif isinstance(node, ast.Call) and _percent_spec(node):
            found.append((node.lineno, "a % format spec in format(), __format__ or a template's format"))
    return sorted(found)


def _fetched_by_a_name_not_a_literal(func: ast.expr) -> bool:
    """getattr(x, name)(...) with a name that is not a literal string: a method fetched by a name the guard cannot read."""
    return (isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "getattr"
            and len(func.args) >= 2 and not (isinstance(func.args[1], ast.Constant) and isinstance(func.args[1].value, str)))


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# The methods that fill a template's fields: str's own, and a string.Formatter's
TEMPLATE_METHODS = frozenset({"format", "format_map", "vformat"})


def _percent_spec(call: ast.Call) -> bool:
    """A literal format spec with a %, which a datetime hands to strftime: format(x, "%Y"), x.__format__("%Y"), or a
    literal template with such a field, the template being what the method is called on ("{:%Y}".format(x), its
    format_map) or its first argument (str.format("{:%Y}", x), a Formatter's format or vformat)."""
    func, args = call.func, call.args
    if isinstance(func, ast.Name) and func.id == "format" and len(args) >= 2:
        return "%" in (_literal(args[1]) or "")
    if isinstance(func, ast.Attribute) and func.attr == "__format__" and args:
        return "%" in (_literal(args[0]) or "")
    if isinstance(func, ast.Attribute) and func.attr in TEMPLATE_METHODS:
        template = _literal(func.value) if _literal(func.value) is not None else _literal(args[0]) if args else None
        try:
            return template is not None and any("%" in (spec or "") for _, _, spec, _ in string.Formatter().parse(template))
        except ValueError:  # not a template the method could fill: no field to hand a spec to
            return False
    return False


def test_the_guarded_modules_are_the_ones_whose_functions_write_a_capture_time() -> None:
    # The list is pinned to the writers themselves: a module dropped from it, or a writer moved, fails here
    from services.contracts.ingest import format_utc
    from services.ingest.metadata import _format_local
    from services.media.discovery import normalize_creation_utc

    writers = {Path(inspect.getfile(function)).resolve().relative_to(ROOT).as_posix()
               for function in (format_utc, _format_local, normalize_creation_utc)}
    assert set(GUARDED) == writers and len(GUARDED) == len(writers)


@pytest.mark.parametrize("module", GUARDED)
def test_no_module_that_writes_a_capture_time_leaves_its_year_to_the_platform(module: str) -> None:
    assert offences((ROOT / module).read_text(encoding="utf-8")) == []


# Every form the guard refuses, as its docstring says, and so the whole of what it claims: each body is planted as a
# function at the end of each guarded module and must be refused there
REFUSED_FORMS = {
    "strftime as an attribute": 'return m.strftime("%Y")',
    "strftime as an attribute of a class": 'from datetime import datetime as D\n    return D.strftime(m, "%Y")',
    "strftime as a name": 'return strftime("%Y")',
    "an import of strftime": "from time import strftime\n    return 0",
    "an import of strftime, aliased": "from time import strftime as s\n    return s",
    "a string naming strftime": 'return getattr(m, "strftime")("%Y")',
    "a string naming strftime in two literals side by side": 'return getattr(m, "strf" "time")("%Y")',
    "the locale's nl_langinfo as an attribute": "import locale\n    return locale.nl_langinfo(locale.CODESET)",
    "a locale format as an attribute": "import locale\n    return locale.D_T_FMT",
    "a locale format as a name": "return D_FMT",
    "each locale name by import": ("from locale import nl_langinfo, D_T_FMT, D_FMT, T_FMT, T_FMT_AMPM, ERA_D_T_FMT, "
                                   "ERA_D_FMT, ERA_T_FMT\n    return 0"),
    "a star import": "from time import *\n    return 0",
    "a method fetched by a name not a literal, called at once": "name = 'x'\n    return getattr(m, name)()",
    "an f-string's % spec": 'return f"{m:%Y-%m-%d}"',
    "an f-string's % spec in a field nested in its spec": "return f\"{m:{'%Y'}}\"",
    "format() with a % spec": 'return format(m, "%Y")',
    "__format__ with a % spec": 'return m.__format__("%Y")',
    "a literal template's format with a % spec": 'return "{:%Y}".format(m)',
    "a literal template's format, its field named": 'return "{when:%Y-%m}".format(when=m)',
    "a literal template's format_map": 'return "{x:%Y}".format_map({"x": m})',
    "str.format with a literal template": 'return str.format("{:%Y}", m)',
    "a string.Formatter's format with a literal template": 'import string\n    return string.Formatter().format("{:%Y}", m)',
    "a string.Formatter's vformat with a literal template": ('import string\n'
                                                             '    return string.Formatter().vformat("{:%Y}", (m,), {})'),
}


@pytest.mark.parametrize("module", GUARDED)
@pytest.mark.parametrize("form", sorted(REFUSED_FORMS))
def test_each_form_the_guard_claims_is_refused_in_each_guarded_module(form: str, module: str) -> None:
    text = (ROOT / module).read_text(encoding="utf-8")
    planted_from = len(text.splitlines()) + 1
    planted = text + "\n\ndef planted(m):\n    " + REFUSED_FORMS[form] + "\n"
    assert [what for line, what in offences(planted) if line > planted_from], form


# Each check of the guard, with a source only it refuses, so removing the check fails its row
ONE_CHECK_EACH = [
    ('def f(m):\n    return m.strftime("%d")\n', "the attribute strftime"),
    ('def f():\n    return strftime("%d")\n', "the name strftime"),
    ('from time import strftime as s\n', "an import of strftime"),
    ('from time import *\n', "a star import"),
    ('def f(m):\n    return getattr(m, "strftime")\n', "a string naming strftime"),
    ('def f(m, name):\n    return getattr(m, name)("%d")\n', "a method fetched by a name that is not a literal"),
    ('def f(lang):\n    return lang.nl_langinfo(0)\n', "the attribute nl_langinfo"),
    ('def f():\n    return D_T_FMT\n', "the name D_T_FMT"),
    ('from locale import D_FMT as d\n', "an import of D_FMT"),
    ('def f(m):\n    return f"{m:%d}"\n', "an f-string's % format spec"),
    ('def f(m):\n    return format(m, "%d")\n', "a % format spec in format(), __format__ or a template's format"),
    ('def f(m):\n    return "{:%d}".format(m)\n', "a % format spec in format(), __format__ or a template's format"),
]


@pytest.mark.parametrize(("source", "check"), ONE_CHECK_EACH, ids=[f"{check} {n}" for n, (_, check) in enumerate(ONE_CHECK_EACH)])
def test_each_check_of_the_guard_refuses_its_own_row(source: str, check: str) -> None:
    assert [what for _, what in offences(source)] == [check]


# The locale's names the guard must refuse, written out apart from LOCALE_FORMATS, so a name dropped from the guard fails
LOCALE_NAMES = ["nl_langinfo", "D_T_FMT", "D_FMT", "T_FMT", "T_FMT_AMPM", "ERA_D_T_FMT", "ERA_D_FMT", "ERA_T_FMT"]


@pytest.mark.parametrize("name", LOCALE_NAMES)
def test_each_locale_format_is_refused_by_name_and_by_import(name: str) -> None:
    assert [what for _, what in offences(f"def f(locale):\n    return locale.{name}\n")] == [f"the attribute {name}"]
    assert [what for _, what in offences(f"from locale import {name}\n")] == [f"an import of {name}"]


def test_the_guard_takes_python_s_own_years_and_says_nothing_of_comments_or_docstrings() -> None:
    clean = (
        '"""A module whose docstring names strftime and %Y."""\n'
        "# a comment that names strftime and %Y\n"
        "def f(m):\n"
        '    """Writes m without strftime, its %Y year code being the platform\'s."""\n'
        '    return m.replace(microsecond=0).isoformat() + f"{m.year:04d}" + "%d %H" % (1, 2) + format(m.year, "04d")\n'
        '\n'
        'def g(m, field):\n'
        '    return getattr(m, "isoformat")() + "{:04d}".format(m.year) + f"{m.month:02d}" + str(getattr(m, field))\n'
        '\n'
        'def h(m, words):\n'
        '    # A % outside every field, a field with no spec, a spec with no %, and a template that cannot be filled\n'
        '    return ("{:d} at 50%".format(3) + "{}%".format(m.year) + str.format("{:>6}", m.year)\n'
        '            + "{x:04d}".format_map({"x": m.year}) + m.year.__format__("04d") + words.format("{"))\n'
    )
    assert offences(clean) == []


# ---- The real protection: a year below 1000 through every function that writes a capture time ----
# Run in the lead's Linux container under Python 3.12 (the test ids are in the report); on a Mac it passes either way.
# predicted_sky's frame_moment, a helper outside the three modules (the SKY lane's), still writes its year with
# strftime: where the platform does not pad a year below 1000 it is expected to fail, by a check made here, not assumed

PLATFORM_PADS = datetime(999, 1, 1).strftime("%Y") == "0999"


def test_each_capture_time_writer_puts_a_year_below_1000_in_four_digits() -> None:
    from PIL import ExifTags, Image

    from services.contracts.ingest import format_utc
    from services.ingest.metadata import _format_local, exif_hints, quicktime_hints
    from services.media.discovery import normalize_creation_utc

    assert format_utc(datetime(1, 1, 1, tzinfo=timezone.utc)) == "0001-01-01T00:00:00Z"
    assert format_utc(datetime(999, 12, 31, 23, 59, 59, 5000, tzinfo=timezone.utc)) == "0999-12-31T23:59:59.005Z"
    assert _format_local(datetime(1, 1, 1)) == "0001-01-01T00:00:00"
    assert _format_local(datetime(999, 12, 31, 23, 59, 59)) == "0999-12-31T23:59:59"
    assert normalize_creation_utc("0001-01-01T00:00:00Z") == "0001-01-01T00:00:00Z"
    assert normalize_creation_utc("0999-12-31T23:59:59.5+0100") == "0999-12-31T22:59:59.500Z"
    # Through the readers, as an upload's record holds it
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "0999:12:31 23:59:59"
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.OffsetTimeOriginal] = "+01:00"
    hint = exif_hints(exif)["captureTime"]
    assert (hint["localDateTime"], hint["utc"]) == ("0999-12-31T23:59:59", "0999-12-31T22:59:59Z")
    hint = quicktime_hints({"com.apple.quicktime.creationdate": "0999-12-31T23:59:59+0100"})["captureTime"]
    assert (hint["localDateTime"], hint["utc"]) == ("0999-12-31T23:59:59", "0999-12-31T22:59:59Z")


# Who takes this mark out (the lead's answers to SKY and CATALOG): catalog-1's fourth version and sky-4 each fix the
# helper; whichever brings the fix onto a main that already holds this file removes the mark, and if this file lands
# after the fix, the TIME lane drops it. Strict, so a fixed helper fails here until the mark goes
@pytest.mark.xfail(not PLATFORM_PADS, reason="predicted_sky.frame_moment writes its year with strftime, which this "
                   "platform does not pad below year 1000 (catalog-1 v4 or sky-4 fixes it)", raises=AssertionError,
                   strict=True)
def test_the_frame_moment_helper_puts_a_year_below_1000_in_four_digits() -> None:
    from services.science.predicted_sky import frame_moment

    assert frame_moment("0999-12-31T23:59:59Z", 0.0) == "0999-12-31T23:59:59Z"
