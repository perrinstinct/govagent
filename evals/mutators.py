"""One deterministic mutator per auto-fixable rule: inject a violation into a lint-clean spec.

A mutator picks an eligible location with the given RNG (seeded, so datasets are reproducible)
and returns the JSON Patch ops that inject the violation plus the pointer where the linter is
expected to report it. Ops are applied with `core.patching`, so comments and formatting of the
seed survive and renames keep their position.

`MUTATORS` is in canonical application order: when several mutations are combined, applying
them in this order keeps every expected pointer valid (paths are renamed first, a response is
removed before another mutation could target it, ...).
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from govagent.core.openapi import HTTP_METHODS
from govagent.core.pointers import format_pointer
from govagent.domain.models import PatchOp

KEBAB_PATH = re.compile(r"^(/([a-z0-9]+(-[a-z0-9]+)*|\{[^/{}]+\}))*/?$")
CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
ERROR_CODE = re.compile(r"^[45]([0-9]{2}|XX)$")
CLIENT_ERROR_CODE = re.compile(r"^4([0-9]{2}|XX)$")
PROBLEM_JSON = "application/problem+json"


@dataclass(frozen=True)
class Mutation:
    rule_id: str
    pointer: str  # where the linter must report the injected violation
    ops: list[PatchOp]


Mutator = Callable[[Any, random.Random], Mutation | None]


def _op(**fields: Any) -> PatchOp:
    return PatchOp.model_validate(fields)


def _move(source: str, target: str) -> PatchOp:
    return PatchOp.model_validate({"op": "move", "from": source, "path": target})


def _operations(root: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """(path key, method, operation) in document order."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in cast(dict[str, Any], root.get("paths", {})).items():
        for method, operation in cast(dict[str, Any], item).items():
            if method in HTTP_METHODS:
                found.append((path, method, cast(dict[str, Any], operation)))
    return found


def _pick[T](rng: random.Random, candidates: list[T]) -> T | None:
    return rng.choice(candidates) if candidates else None


def _rename_path(
    root: Any, rng: random.Random, rule_id: str, rename: Callable[[str], str]
) -> Mutation | None:
    paths = cast(dict[str, Any], root.get("paths", {}))
    candidates: list[tuple[str, str]] = []
    for path in paths:
        new = rename(path)
        if KEBAB_PATH.match(path) and not path.endswith("/") and new != path and new not in paths:
            candidates.append((path, new))
    picked = _pick(rng, candidates)
    if picked is None:
        return None
    old, new = picked
    pointer = format_pointer(["paths", new])
    return Mutation(rule_id, pointer, [_move(format_pointer(["paths", old]), pointer)])


def _camel_segment(path: str) -> str:
    """First hyphenated static segment -> camelCase; otherwise capitalize the first one."""
    segments = path.split("/")
    static = [i for i, s in enumerate(segments) if s and not s.startswith("{")]
    for i in static:
        if "-" in segments[i]:
            head, *tail = segments[i].split("-")
            segments[i] = head + "".join(word.capitalize() for word in tail)
            return "/".join(segments)
    if static:
        segments[static[0]] = segments[static[0]].capitalize()
    return "/".join(segments)


def path_kebab(root: Any, rng: random.Random) -> Mutation | None:
    return _rename_path(root, rng, "gov-path-kebab", _camel_segment)


def path_no_trailing_slash(root: Any, rng: random.Random) -> Mutation | None:
    return _rename_path(root, rng, "gov-path-no-trailing-slash", lambda path: path + "/")


def servers_https(root: Any, rng: random.Random) -> Mutation | None:
    servers = cast(list[dict[str, Any]], root.get("servers", []))
    candidates = [
        i for i, server in enumerate(servers) if str(server.get("url", "")).startswith("https://")
    ]
    index = _pick(rng, candidates)
    if index is None:
        return None
    pointer = format_pointer(["servers", index, "url"])
    url = "http://" + str(servers[index]["url"]).removeprefix("https://")
    return Mutation("gov-servers-https", pointer, [_op(op="replace", path=pointer, value=url)])


def _remove_operation_field(
    root: Any, rng: random.Random, rule_id: str, field: str
) -> Mutation | None:
    candidates = [(p, m) for p, m, operation in _operations(root) if field in operation]
    picked = _pick(rng, candidates)
    if picked is None:
        return None
    operation_pointer = format_pointer(["paths", *picked])
    remove = _op(op="remove", path=f"{operation_pointer}/{field}")
    return Mutation(rule_id, operation_pointer, [remove])


def operation_id(root: Any, rng: random.Random) -> Mutation | None:
    return _remove_operation_field(root, rng, "gov-operation-id", "operationId")


def operation_summary(root: Any, rng: random.Random) -> Mutation | None:
    return _remove_operation_field(root, rng, "gov-operation-summary", "summary")


def operation_tags(root: Any, rng: random.Random) -> Mutation | None:
    return _remove_operation_field(root, rng, "gov-operation-tags", "tags")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def operation_id_camel(root: Any, rng: random.Random) -> Mutation | None:
    candidates: list[tuple[str, str, str]] = []
    for path, method, operation in _operations(root):
        current = str(operation.get("operationId", ""))
        if CAMEL.match(current) and _snake(current) != current:
            candidates.append((path, method, current))
    picked = _pick(rng, candidates)
    if picked is None:
        return None
    path, method, current = picked
    pointer = format_pointer(["paths", path, method, "operationId"])
    replace = _op(op="replace", path=pointer, value=_snake(current))
    return Mutation("gov-operation-id-camel", pointer, [replace])


def error_response(root: Any, rng: random.Random) -> Mutation | None:
    candidates: list[tuple[str, str, list[str]]] = []
    for path, method, operation in _operations(root):
        codes = [str(code) for code in cast(dict[Any, Any], operation.get("responses", {}))]
        client_errors = [code for code in codes if CLIENT_ERROR_CODE.match(code)]
        if client_errors and len(client_errors) < len(codes):  # keep at least one response
            candidates.append((path, method, client_errors))
    picked = _pick(rng, candidates)
    if picked is None:
        return None
    path, method, client_errors = picked
    responses = format_pointer(["paths", path, method, "responses"])
    ops = [
        _op(op="remove", path=f"{responses}/{format_pointer([code])[1:]}") for code in client_errors
    ]
    return Mutation("gov-error-response", responses, ops)


def problem_json(root: Any, rng: random.Random) -> Mutation | None:
    """Serve an error body as application/json: inline in an operation, or in a shared response."""
    candidates: list[list[str | int]] = []
    referenced: list[str] = []
    for path, method, operation in _operations(root):
        for code, response in cast(dict[Any, Any], operation.get("responses", {})).items():
            if not ERROR_CODE.match(str(code)):
                continue
            ref = cast(dict[str, Any], response).get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/responses/"):
                referenced.append(ref.removeprefix("#/components/responses/"))
            elif PROBLEM_JSON in cast(dict[str, Any], response).get("content", {}):
                candidates.append(["paths", path, method, "responses", str(code), "content"])
    shared = cast(dict[str, Any], root.get("components", {}).get("responses", {}))
    for name in dict.fromkeys(referenced):  # document order, no duplicates
        if PROBLEM_JSON in cast(dict[str, Any], shared.get(name, {})).get("content", {}):
            candidates.append(["components", "responses", name, "content"])
    content = _pick(rng, candidates)
    if content is None:
        return None
    pointer = format_pointer(content)
    move = _move(f"{pointer}/application~1problem+json", f"{pointer}/application~1json")
    return Mutation("gov-problem-json", pointer, [move])


def _schemas_with_properties(
    node: Any, path: list[str | int]
) -> list[tuple[list[str | int], dict[str, Any]]]:
    """Schema objects (with `properties`) under components/schemas, nested ones included."""
    found: list[tuple[list[str | int], dict[str, Any]]] = []
    if isinstance(node, dict):
        schema = cast(dict[str, Any], node)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            found.append((path, schema))
            for name, child in cast(dict[str, Any], properties).items():
                found.extend(_schemas_with_properties(child, [*path, "properties", name]))
    return found


def property_camel(root: Any, rng: random.Random) -> Mutation | None:
    candidates: list[tuple[list[str | int], dict[str, Any], str, str]] = []
    for name, schema in cast(dict[str, Any], root.get("components", {}).get("schemas", {})).items():
        for path, owner in _schemas_with_properties(schema, ["components", "schemas", name]):
            properties = cast(dict[str, Any], owner["properties"])
            for prop in properties:
                if not CAMEL.match(prop):
                    continue
                new = _snake(prop) if _snake(prop) != prop else prop.capitalize()
                if new not in properties:
                    candidates.append((path, owner, prop, new))
    picked = _pick(rng, candidates)
    if picked is None:
        return None
    path, owner, old, new = picked
    schema_pointer = format_pointer(path)
    pointer = f"{schema_pointer}/properties/{format_pointer([new])[1:]}"
    ops = [_move(f"{schema_pointer}/properties/{format_pointer([old])[1:]}", pointer)]
    for index, name in enumerate(cast(list[Any], owner.get("required", []))):
        if name == old:  # keep the spec consistent: the fix must rename both
            ops.append(_op(op="replace", path=f"{schema_pointer}/required/{index}", value=new))
    return Mutation("gov-property-camel", pointer, ops)


MUTATORS: dict[str, Mutator] = {
    "gov-path-kebab": path_kebab,
    "gov-path-no-trailing-slash": path_no_trailing_slash,
    "gov-servers-https": servers_https,
    "gov-operation-id": operation_id,
    "gov-operation-id-camel": operation_id_camel,
    "gov-operation-summary": operation_summary,
    "gov-operation-tags": operation_tags,
    "gov-error-response": error_response,
    "gov-problem-json": problem_json,
    "gov-property-camel": property_camel,
}
