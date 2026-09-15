"""Regression test for the real string-escaping bug found during live
Phase 4 validation (docs/research.md section 10): substituting a
json.dumps()-encoded config directly into an already-quoted
`json.loads("...")` template slot produced invalid Python the moment a
value contained a quote character. Verifies the fix by actually compiling
the generated script -- not just checking string equality."""
import ast
import json

from controller.services.colab_driver import _BOOTSTRAP_TEMPLATE


def _build_script(config: dict) -> str:
    model_config_json = json.dumps(config)
    escaped_literal = json.dumps(model_config_json)[1:-1]
    return _BOOTSTRAP_TEMPLATE.replace("__MODEL_CONFIG_JSON__", escaped_literal)


def test_bootstrap_script_is_valid_python_for_plain_model_id():
    script = _build_script({"model_id": "Qwen/Qwen2.5-0.5B-Instruct", "revision": None,
                             "dtype": "bfloat16", "max_model_len": 4096})
    ast.parse(script)  # raises SyntaxError if malformed


def test_bootstrap_script_survives_quotes_and_backslashes_in_config():
    """The original bug: any double quote in the substituted value broke
    the surrounding json.loads("...") string literal."""
    tricky = {
        "model_id": 'weird/model"with-quote',
        "revision": "back\\slash",
        "dtype": "bfloat16",
        "max_model_len": 4096,
    }
    script = _build_script(tricky)
    ast.parse(script)  # must not raise SyntaxError


def test_bootstrap_script_docstring_marker_not_corrupted():
    """The `.replace()` call is a plain substring replace across the whole
    template -- guard against it also matching (and corrupting) any prose
    mention of the marker name elsewhere in the file."""
    script = _build_script({"model_id": "m", "revision": None, "dtype": "bfloat16", "max_model_len": 1})
    assert "__MODEL_CONFIG_JSON__" not in script
    ast.parse(script)


def test_embedded_config_round_trips_through_json_loads():
    config = {"model_id": "a/b", "revision": "v1", "dtype": "float16", "max_model_len": 2048}
    script = _build_script(config)
    tree = ast.parse(script)
    # find the `_CONFIG = json.loads("...")` assignment and eval its RHS literal
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_CONFIG" for t in node.targets
        ):
            literal = ast.literal_eval(node.value.args[0])
            assert json.loads(literal) == config
            return
    raise AssertionError("_CONFIG assignment not found in generated script")
