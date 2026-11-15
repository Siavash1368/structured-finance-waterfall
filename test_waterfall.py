"""Three checks on the teaching model. Run with: pytest"""

from waterfall import DEAL, SCENARIOS, run_pool, run_waterfall


def test_cash_in_equals_cash_out_in_every_scenario():
    for scenario in SCENARIOS.values():
        pool = run_pool(DEAL, scenario["cpr"], scenario["cdr"], scenario["severity"])
        for tests_on in [False, True]:
            waterfall = run_waterfall(DEAL, pool, tests_on)
            cash_in = (pool["interest"] + pool["scheduled_principal"]
                       + pool["prepayment"] + pool["recovery_received"])
            cash_out = (waterfall["servicing_fee"]
                        + waterfall["a_interest_paid"] + waterfall["b_interest_paid"]
                        + waterfall["c_interest_paid"] + waterfall["residual_interest"]
                        + waterfall["a_principal_paid"] + waterfall["b_principal_paid"]
                        + waterfall["c_principal_paid"] + waterfall["residual_principal"])
            assert (cash_in - cash_out).abs().max() < 0.01


def test_no_balance_is_ever_negative():
    # Balances are floats, so rounding noise below half a cent is allowed.
    for scenario in SCENARIOS.values():
        pool = run_pool(DEAL, scenario["cpr"], scenario["cdr"], scenario["severity"])
        assert pool["ending_balance"].min() > -0.005
        assert pool["recoveries_pending"].min() > -0.005
        for tests_on in [False, True]:
            waterfall = run_waterfall(DEAL, pool, tests_on)
            assert waterfall["a_balance"].min() > -0.005
            assert waterfall["b_balance"].min() > -0.005
            assert waterfall["c_balance"].min() > -0.005


def test_no_prepayments_and_no_defaults_matches_the_level_pay_formula():
    pool = run_pool(DEAL, cpr=0.0, cdr=0.0, severity=0.35)
    waterfall = run_waterfall(DEAL, pool, tests_on=True)

    month_1_payment = pool.loc[1, "interest"] + pool.loc[1, "scheduled_principal"]
    assert abs(month_1_payment - 665_302.50) < 0.01

    assert waterfall["a_writedown"].sum() == 0
    assert waterfall["b_writedown"].sum() == 0
    assert waterfall["c_writedown"].sum() == 0

    assert abs(waterfall["a_principal_paid"].sum() - DEAL["a_balance"]) < 0.01
    assert abs(waterfall["b_principal_paid"].sum() - DEAL["b_balance"]) < 0.01
    assert abs(waterfall["c_principal_paid"].sum() - DEAL["c_balance"]) < 0.01
    assert abs(waterfall["residual_principal"].sum() - DEAL["residual_balance"]) < 0.01
