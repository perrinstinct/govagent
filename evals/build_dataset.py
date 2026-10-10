"""Build the eval dataset from the seeds: `uv run python -m evals.build_dataset`.

- single-violation cases: every seed x every mutator;
- multi-violation cases: per seed, combinations of 3-5 mutators applied in canonical order.

Each case stores the mutated spec and the expected violations (ground truth). Building checks
every case with Spectral (dataset sanity): the linter must report exactly the injected
violations and the mutated spec must be valid OpenAPI, otherwise the build fails.
Everything is derived from `--seed`, so the same seed always produces the same dataset.
"""

import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from evals.mutators import MUTATORS, Mutation
from govagent.adapters.spectral import SpectralLinter
from govagent.core.patching import apply_patch
from govagent.core.spec_io import SpecDocument, dump_spec, load_spec
from govagent.domain.ports import Linter

EVALS_DIR = Path(__file__).resolve().parent
SEEDS_DIR = EVALS_DIR / "seeds"
DATASET_PATH = EVALS_DIR / "dataset.jsonl"
REPO_ROOT = EVALS_DIR.parent
MULTI_CASES_PER_SEED = 4


class InjectedViolation(BaseModel, frozen=True):
    rule_id: str
    pointer: str

    @property
    def fingerprint(self) -> str:
        return f"{self.rule_id}:{self.pointer}"


class Case(BaseModel, frozen=True):
    id: str
    seed: str
    kind: str  # "single" | "multi"
    injected: list[InjectedViolation]
    spec_text: str


def seed_files() -> list[Path]:
    return sorted(p for p in SEEDS_DIR.iterdir() if p.suffix in (".yaml", ".json"))


def mutate(spec_text: str, rules: Sequence[str], rng: random.Random) -> tuple[str, list[Mutation]]:
    """Apply the mutators of `rules` in canonical order; skip those with no eligible location."""
    spec = load_spec(spec_text)
    root = spec.root
    applied: list[Mutation] = []
    for rule_id in (r for r in MUTATORS if r in rules):
        mutation = MUTATORS[rule_id](root, rng)
        if mutation is not None:
            root = apply_patch(root, mutation.ops)
            applied.append(mutation)
    return dump_spec(SpecDocument(root, spec.style)), applied


def build_cases(dataset_seed: int) -> list[Case]:
    cases: list[Case] = []
    for seed in seed_files():
        text = seed.read_text()
        for rule_id in MUTATORS:
            case_id = f"{seed.stem}--{rule_id.removeprefix('gov-')}"
            mutated, applied = mutate(text, [rule_id], random.Random(f"{dataset_seed}:{case_id}"))
            if applied:
                cases.append(_case(case_id, seed, "single", mutated, applied))
        for index in range(MULTI_CASES_PER_SEED):
            case_id = f"{seed.stem}--multi-{index + 1}"
            rng = random.Random(f"{dataset_seed}:{case_id}")
            rules = rng.sample(list(MUTATORS), rng.randint(3, 5))
            mutated, applied = mutate(text, rules, rng)
            if len(applied) >= 3:
                cases.append(_case(case_id, seed, "multi", mutated, applied))
    return cases


def _case(case_id: str, seed: Path, kind: str, text: str, applied: list[Mutation]) -> Case:
    injected = [InjectedViolation(rule_id=m.rule_id, pointer=m.pointer) for m in applied]
    return Case(id=case_id, seed=seed.name, kind=kind, injected=injected, spec_text=text)


def check_cases(cases: Sequence[Case], linter: Linter) -> list[str]:
    """Dataset sanity: one message per case whose lint differs from its ground truth."""
    problems: list[str] = []
    for case in cases:
        load_spec(case.spec_text)  # raises if a mutation produced an invalid document
        found = sorted(v.fingerprint for v in linter.lint(case.spec_text))
        expected = sorted(v.fingerprint for v in case.injected)
        if found != expected:
            problems.append(f"{case.id}: expected {expected}, linter found {found}")
    return problems


def smoke_subset(cases: Sequence[Case]) -> list[Case]:
    """One single case per rule and two multi cases, spread over the seeds (round-robin)."""
    seeds = sorted({case.seed for case in cases})
    by_id = {case.id: case for case in cases}
    picked: list[Case] = []
    for index, rule_id in enumerate(MUTATORS):
        for offset in range(len(seeds)):  # rule i -> seed i, or the next seed that has it
            stem = Path(seeds[(index + offset) % len(seeds)]).stem
            case = by_id.get(f"{stem}--{rule_id.removeprefix('gov-')}")
            if case is not None:
                picked.append(case)
                break
    multi = [case for case in cases if case.kind == "multi" and case.id.endswith("--multi-1")]
    return [*picked, *multi[-2:]]


def load_cases(path: Path = DATASET_PATH) -> list[Case]:
    return [Case.model_validate_json(line) for line in path.read_text().splitlines() if line]


def main(
    seed: Annotated[int, typer.Option(help="Dataset seed: same seed, same dataset.")] = 42,
    output: Annotated[Path, typer.Option(help="Where to write the JSONL dataset.")] = DATASET_PATH,
    check: Annotated[
        bool, typer.Option("--check", help="Fail if the committed dataset is out of date.")
    ] = False,
) -> None:
    cases = build_cases(seed)
    linter = SpectralLinter("spectral", REPO_ROOT / "rulesets" / "governance.spectral.yaml")
    problems = check_cases(cases, linter)
    if problems:
        typer.echo("dataset sanity check failed:\n" + "\n".join(problems), err=True)
        raise typer.Exit(1)
    content = "".join(case.model_dump_json() + "\n" for case in cases)
    if check:
        if not output.exists() or output.read_text() != content:
            typer.echo(f"{output} is out of date: run `python -m evals.build_dataset`", err=True)
            raise typer.Exit(1)
        typer.echo(f"{output} is up to date ({len(cases)} cases)")
        return
    output.write_text(content)
    kinds = {kind: sum(c.kind == kind for c in cases) for kind in ("single", "multi")}
    injected = sum(len(c.injected) for c in cases)
    typer.echo(
        f"wrote {len(cases)} cases ({kinds['single']} single, {kinds['multi']} multi, "
        f"{injected} injected violations) to {output}; sanity check passed"
    )
    typer.echo(json.dumps({"smoke": [c.id for c in smoke_subset(cases)]}))


if __name__ == "__main__":
    typer.run(main)
