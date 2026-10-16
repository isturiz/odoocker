"""Small capability checks for the installed Odoo, not a release dispatch table."""

import ast
import inspect
import sys
import textwrap
from pathlib import Path

from .errors import OdxError


def configure(config, args, *, init_logger=None):
    try:
        parameters = inspect.signature(config.parse_config).parameters
    except (TypeError, ValueError) as error:
        raise OdxError("Cannot inspect Odoo's configuration API") from error
    logging = parameters.get("setup_logging")
    if logging and logging.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        config.parse_config(args, setup_logging=True)
    else:
        if not callable(init_logger):
            raise OdxError("Odoo has no recognized logging initialization capability")
        # Do not retry TypeError: a failure INSIDE parse_config is a real failure.
        config.parse_config(args)
        init_logger()


def without_demo_argument(config):
    parser = getattr(config, "parser", None)
    get_option = getattr(parser, "get_option", None)
    option = get_option("--without-demo") if callable(get_option) else None
    if option is not None:
        action, dest = option.action, option.dest
        if (action, dest) in (("store_false", "with_demo"), ("store_true", "without_demo")):
            return "--without-demo"
        if option.type == "without_demo" and dest == "with_demo":
            return "--without-demo=true"
        if (action, dest, option.type, option.nargs) == ("store", "without_demo", "string", 1):
            return "--without-demo=all"
    raise OdxError("Odoo has no recognized --without-demo capability; refusing to guess")


def loadable_module_names(module):
    domain = getattr(module, "_get_modules_to_load_domain", None)
    if not callable(domain):
        raise OdxError("Odoo has no recognized loadable-module domain capability")
    return module.search(domain()).mapped("name")


def registration_boundary(load_modules):
    """Locate the model-hook loop structurally, regardless of layout or variable names.

    This is an internal API check, not a promise of universal compatibility. An
    absent/ambiguous boundary must stop diagnosis before normal startup is attempted.
    """
    try:
        if inspect.unwrap(load_modules) is not load_modules:
            raise OdxError("Wrapped Odoo module loaders are not a recognized diagnostic capability")
        lines, start = inspect.getsourcelines(load_modules)
        tree = ast.parse(textwrap.dedent("".join(lines)))
    except (OSError, TypeError, SyntaxError) as error:
        raise OdxError("Cannot inspect Odoo's model registration boundary") from error
    roots = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(roots) != 1:
        raise OdxError("Cannot identify Odoo's module loading function")

    def own_nodes(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            yield node
            yield from own_nodes(ast.iter_child_nodes(node))

    nodes = list(own_nodes(roots[0].body))
    hook_calls = [
        node
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_register_hook"
    ]
    candidates = []
    for node in nodes:
        if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name)):
            continue
        iterator = node.iter
        if not (
            isinstance(iterator, ast.Call)
            and isinstance(iterator.func, ast.Attribute)
            and iterator.func.attr == "values"
            and isinstance(iterator.func.value, ast.Name)
            and not iterator.args
            and not iterator.keywords
        ):
            continue
        for statement in node.body:
            call = statement.value if isinstance(statement, ast.Expr) else None
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_register_hook"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == node.target.id
            ):
                candidates.append((start + node.lineno - 1, iterator.func.value.id))
                break
    if len(candidates) != 1 or len(hook_calls) != 1:
        raise OdxError("Unsupported/ambiguous Odoo registration boundary; cannot safely diagnose")
    return candidates[0]


def check_python_minimum(source, *, version=None):
    """Read literal runtime requirements without importing/executing checkout code.

    Some checkouts declare the minimum in release metadata, others in package
    initialization. Missing metadata is not evidence of compatibility: Odoo's own
    import checks and dependency resolution still apply.
    """
    minimums = set()
    for relative in ("odoo/release.py", "odoo/__init__.py", "odoo/init.py"):
        file = Path(source) / relative
        if not file.is_file():
            continue
        try:
            tree = ast.parse(file.read_text())
        except (OSError, SyntaxError) as error:
            raise OdxError("Cannot inspect Odoo's Python requirements") from error
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(n, ast.Name) and n.id == "MIN_PY_VERSION" for n in node.targets):
                continue
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError) as error:
                raise OdxError(
                    "Odoo's minimum Python requirement is not a literal tuple"
                ) from error
            if not (
                isinstance(value, tuple)
                and 2 <= len(value) <= 3
                and all(type(n) is int and n >= 0 for n in value)
            ):
                raise OdxError("Invalid Odoo minimum Python metadata")
            minimums.add(value)
    if len(minimums) > 1:
        raise OdxError("Conflicting Odoo minimum Python requirements")
    if minimums:
        minimum = minimums.pop()
        actual = tuple(version if version is not None else sys.version_info[:3])
        if actual[: len(minimum)] < minimum:
            required = ".".join(map(str, minimum))
            raise OdxError(f"Odoo source requires Python >= {required}; this runtime is too old")
        return minimum
    return None
