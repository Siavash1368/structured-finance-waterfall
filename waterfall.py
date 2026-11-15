"""A small teaching model of a securitization.

Pool cash flows -> payment waterfall -> coverage tests -> results per tranche.
Every number in DEAL and SCENARIOS is an illustrative assumption. It is not
taken from any real deal.
"""

import pandas as pd

# Illustrative deal assumptions (not a real deal). Balances in dollars, rates per year.
DEAL = {
    "pool_balance": 100_000_000,
    "pool_coupon": 0.07,
    "pool_term_months": 360,
    "servicing_fee_rate": 0.005,
    "recovery_lag_months": 6,
    "a_balance": 80_000_000,
    "a_coupon": 0.05,
    "b_balance": 10_000_000,
    "b_coupon": 0.0575,
    "c_balance": 6_000_000,
    "c_coupon": 0.065,
    "residual_balance": 4_000_000,
    "oc_trigger": 1.08,
    "ic_trigger": 1.25,
}

# Illustrative stress assumptions (not from any real deal): CPR, CDR, severity per year.
SCENARIOS = {
    "base": {"cpr": 0.10, "cdr": 0.01, "severity": 0.35},
    "high_default": {"cpr": 0.10, "cdr": 0.03, "severity": 0.35},
    "high_severity": {"cpr": 0.10, "cdr": 0.01, "severity": 0.55},
    "slow_prepay": {"cpr": 0.05, "cdr": 0.01, "severity": 0.35},
    "combined": {"cpr": 0.05, "cdr": 0.03, "severity": 0.55},
}


def run_pool(deal, cpr, cdr, severity):
    """Month-by-month cash flows of the pool. Returns one row per month."""
    smm = 1 - (1 - cpr) ** (1 / 12)
    mdr = 1 - (1 - cdr) ** (1 / 12)
    monthly_rate = deal["pool_coupon"] / 12
    lag = deal["recovery_lag_months"]
    total_months = deal["pool_term_months"] + lag

    pool_balance = deal["pool_balance"]
    recoveries_pending = 0.0
    recovery_arriving = [0.0] * (total_months + lag + 1)
    rows = []
    for month in range(1, total_months + 1):
        beginning_balance = pool_balance
        remaining_term = deal["pool_term_months"] - month + 1

        # Defaults leave the pool first; interest accrues on what survives.
        defaults = beginning_balance * mdr
        surviving_balance = beginning_balance - defaults
        interest = surviving_balance * monthly_rate

        # Scheduled principal from the level-pay formula on the surviving balance.
        if remaining_term > 0:
            level_payment = surviving_balance * monthly_rate / (1 - (1 + monthly_rate) ** -remaining_term)
            scheduled_principal = level_payment - interest
        else:
            scheduled_principal = 0.0

        # Prepayment is a share of the balance left after scheduled principal.
        prepayment = (surviving_balance - scheduled_principal) * smm

        # Loss is booked now; the recovery arrives as cash after the lag.
        loss = defaults * severity
        recovery = defaults * (1 - severity)
        recovery_arriving[month + lag] = recovery
        recovery_received = recovery_arriving[month]
        recoveries_pending = recoveries_pending + recovery - recovery_received

        ending_balance = beginning_balance - defaults - scheduled_principal - prepayment
        pool_balance = ending_balance

        rows.append({
            "month": month,
            "beginning_balance": beginning_balance,
            "defaults": defaults,
            "interest": interest,
            "scheduled_principal": scheduled_principal,
            "prepayment": prepayment,
            "loss": loss,
            "recovery": recovery,
            "recovery_received": recovery_received,
            "recoveries_pending": recoveries_pending,
            "ending_balance": ending_balance,
        })
    return pd.DataFrame(rows).set_index("month")


def run_waterfall(deal, pool, tests_on):
    """Pay the pool cash through the waterfall each month. Returns one row per month."""
    a_balance = deal["a_balance"]
    b_balance = deal["b_balance"]
    c_balance = deal["c_balance"]
    recoveries_pending_beginning = 0.0
    rows = []
    for month in pool.index:
        pool_row = pool.loc[month]

        # Servicing fee: first claim on the pool interest.
        servicing_fee = min(pool_row["beginning_balance"] * deal["servicing_fee_rate"] / 12, pool_row["interest"])
        interest_cash = pool_row["interest"] - servicing_fee

        # A interest: paid from what is left; any unpaid part is a shortfall.
        a_interest_due = a_balance * deal["a_coupon"] / 12
        a_interest_paid = min(a_interest_due, interest_cash)
        interest_cash = interest_cash - a_interest_paid

        # B interest.
        b_interest_due = b_balance * deal["b_coupon"] / 12
        b_interest_paid = min(b_interest_due, interest_cash)
        interest_cash = interest_cash - b_interest_paid

        # Coverage tests: checked after B interest, on beginning-of-month balances.
        if a_balance + b_balance > 0:
            oc_ratio = (pool_row["beginning_balance"] + recoveries_pending_beginning) / (a_balance + b_balance)
            ic_ratio = (pool_row["interest"] - servicing_fee) / (a_interest_due + b_interest_due)
            tests_pass = oc_ratio >= deal["oc_trigger"] and ic_ratio >= deal["ic_trigger"]
        else:
            oc_ratio = float("nan")
            ic_ratio = float("nan")
            tests_pass = True

        # C interest and residual: paid normally, or redirected to principal when a test fails.
        c_interest_due = c_balance * deal["c_coupon"] / 12
        if tests_on and not tests_pass:
            redirected_interest = interest_cash
            c_interest_paid = 0.0
            residual_interest = 0.0
        else:
            redirected_interest = 0.0
            c_interest_paid = min(c_interest_due, interest_cash)
            residual_interest = interest_cash - c_interest_paid

        # Principal: scheduled + prepayments + recoveries (+ redirected interest), A then B then C then residual.
        principal_cash = (pool_row["scheduled_principal"] + pool_row["prepayment"]
                          + pool_row["recovery_received"] + redirected_interest)
        a_principal_paid = min(a_balance, principal_cash)
        b_principal_paid = min(b_balance, principal_cash - a_principal_paid)
        c_principal_paid = min(c_balance, principal_cash - a_principal_paid - b_principal_paid)
        residual_principal = principal_cash - a_principal_paid - b_principal_paid - c_principal_paid
        a_balance = a_balance - a_principal_paid
        b_balance = b_balance - b_principal_paid
        c_balance = c_balance - c_principal_paid

        # Write-down: notes above the collateral are written off, C first, then B, then A.
        collateral = pool_row["ending_balance"] + pool_row["recoveries_pending"]
        excess_notes = max(a_balance + b_balance + c_balance - collateral, 0.0)
        c_writedown = min(c_balance, excess_notes)
        b_writedown = min(b_balance, excess_notes - c_writedown)
        a_writedown = min(a_balance, excess_notes - c_writedown - b_writedown)
        a_balance = a_balance - a_writedown
        b_balance = b_balance - b_writedown
        c_balance = c_balance - c_writedown

        recoveries_pending_beginning = pool_row["recoveries_pending"]
        rows.append({
            "month": month,
            "servicing_fee": servicing_fee,
            "a_interest_due": a_interest_due,
            "a_interest_paid": a_interest_paid,
            "a_interest_shortfall": a_interest_due - a_interest_paid,
            "b_interest_due": b_interest_due,
            "b_interest_paid": b_interest_paid,
            "b_interest_shortfall": b_interest_due - b_interest_paid,
            "oc_ratio": oc_ratio,
            "ic_ratio": ic_ratio,
            "tests_pass": tests_pass,
            "redirected_interest": redirected_interest,
            "c_interest_due": c_interest_due,
            "c_interest_paid": c_interest_paid,
            "c_interest_shortfall": c_interest_due - c_interest_paid,
            "residual_interest": residual_interest,
            "principal_cash": principal_cash,
            "a_principal_paid": a_principal_paid,
            "b_principal_paid": b_principal_paid,
            "c_principal_paid": c_principal_paid,
            "residual_principal": residual_principal,
            "collateral": collateral,
            "a_writedown": a_writedown,
            "b_writedown": b_writedown,
            "c_writedown": c_writedown,
            "a_balance": a_balance,
            "b_balance": b_balance,
            "c_balance": c_balance,
        })
    return pd.DataFrame(rows).set_index("month")


def summarize_tranches(deal, waterfall):
    """Write-down, write-down % and weighted average life (WAL) for classes A, B and C."""
    rows = []
    for tranche in ["a", "b", "c"]:
        original_balance = deal[tranche + "_balance"]
        principal_paid = waterfall[tranche + "_principal_paid"]
        writedown = waterfall[tranche + "_writedown"].sum()
        if principal_paid.sum() > 0:
            wal_years = (principal_paid * waterfall.index).sum() / principal_paid.sum() / 12
        else:
            wal_years = float("nan")
        rows.append({
            "tranche": tranche.upper(),
            "original_balance": original_balance,
            "principal_paid": principal_paid.sum(),
            "writedown": writedown,
            "writedown_pct": 100 * writedown / original_balance,
            "wal_years": wal_years,
            "interest_shortfall": waterfall[tranche + "_interest_shortfall"].sum(),
        })
    return pd.DataFrame(rows).set_index("tranche")


def break_even_cdr(deal, tranche, tests_on):
    """Lowest CDR, in 0.1% steps, at which the tranche takes its first write-down.

    CPR and severity are held at the base scenario. Returns None if no
    write-down appears up to a CDR of 100%.
    """
    cpr = SCENARIOS["base"]["cpr"]
    severity = SCENARIOS["base"]["severity"]
    for step in range(0, 1001):
        cdr = step / 1000
        pool = run_pool(deal, cpr, cdr, severity)
        waterfall = run_waterfall(deal, pool, tests_on)
        if waterfall[tranche.lower() + "_writedown"].sum() > 0.01:
            return cdr
    return None
