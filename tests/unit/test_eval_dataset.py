from collections import Counter

from evals.build_dataset import build_cases, load_cases, smoke_subset
from evals.mutators import MUTATORS

CASES = load_cases()


def test_committed_dataset_is_up_to_date() -> None:
    # Regenerate with `uv run python -m evals.build_dataset` after changing seeds or mutators.
    assert build_cases(42) == CASES


def test_case_ids_are_unique() -> None:
    assert len({case.id for case in CASES}) == len(CASES)


def test_single_cases_cover_every_mutator_on_every_seed() -> None:
    singles = [case for case in CASES if case.kind == "single"]

    assert all(len(case.injected) == 1 for case in singles)
    assert Counter(case.injected[0].rule_id for case in singles) == {rule: 6 for rule in MUTATORS}


def test_multi_cases_combine_three_to_five_distinct_rules() -> None:
    multis = [case for case in CASES if case.kind == "multi"]

    assert multis
    for case in multis:
        rules = [v.rule_id for v in case.injected]
        assert 3 <= len(rules) <= 5
        assert len(set(rules)) == len(rules)


def test_smoke_subset_covers_every_rule_and_every_seed() -> None:
    smoke = smoke_subset(CASES)

    assert len(smoke) == 12
    assert {v.rule_id for case in smoke for v in case.injected} == set(MUTATORS)
    assert len({case.seed for case in smoke}) == 6
