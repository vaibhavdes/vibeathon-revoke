from eval.expected import PLANTED, PLANTED_ACTIVITY, certifier, compute


def test_answer_key_matches_planted_cases():
    assert compute() == PLANTED
    assert compute(include_activity=True) == PLANTED | PLANTED_ACTIVITY


def test_certifiers_come_from_hr_managers():
    assert certifier("dnair") == "Suresh Menon"
    assert certifier("rgupta") == "Rakesh Iyer"
    assert certifier("pnair") == "Suresh Menon"   # found through her past email
