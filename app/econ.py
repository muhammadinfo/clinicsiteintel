"""Unit-economics overlay — turns the spatial capture estimate into a go/no-go
on the actual decision: does predicted patient flow clear break-even?

break_even_cases = annual fixed cost / contribution per case
projected_cases  = Huff-captured expected-case STOCK × annual conversion rate

All cost/revenue assumptions are explicit and configurable; defaults reflect a
small TMJ/dental-sleep specialty practice (oral-appliance + visit revenue).
"""
from dataclasses import dataclass

DEFAULTS = {
    "rent_per_sf_yr": 33.0,
    "square_feet": 1800.0,
    "buildout_capex": 250000.0,
    "buildout_amort_years": 7.0,
    "annual_labor_overhead": 240000.0,   # staff + non-rent fixed
    "revenue_per_case": 2600.0,          # appliance + titration + follow-up
    "variable_cost_per_case": 650.0,     # lab, materials, billing
    "annual_conversion_rate": 0.20,      # fraction of captured prevalent stock that presents/yr
}


@dataclass
class EconResult:
    fixed_annual: float
    contribution_per_case: float
    break_even_cases: float
    projected_cases: float
    margin_cases: float
    verdict: str
    assumptions: dict


def proforma(huff_captured_cases: float, params: dict = None) -> EconResult:
    p = dict(DEFAULTS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})

    fixed = (p["rent_per_sf_yr"] * p["square_feet"]
             + p["buildout_capex"] / max(p["buildout_amort_years"], 1)
             + p["annual_labor_overhead"])
    contrib = max(p["revenue_per_case"] - p["variable_cost_per_case"], 1.0)
    break_even = fixed / contrib
    projected = (huff_captured_cases or 0) * p["annual_conversion_rate"]
    margin = projected - break_even

    if margin >= 0.5 * break_even:
        verdict = (f"Comfortable — projected ~{projected:,.0f} cases/yr vs. ~{break_even:,.0f} needed "
                   "to break even (healthy cushion).")
    elif margin >= 0:
        verdict = (f"Tight but viable — projected ~{projected:,.0f} cases/yr just clears the "
                   f"~{break_even:,.0f} break-even; little margin for error.")
    else:
        verdict = (f"Below break-even — projected ~{projected:,.0f} cases/yr falls short of the "
                   f"~{break_even:,.0f} needed; unit economics don't close at these assumptions.")

    return EconResult(round(fixed), round(contrib), round(break_even), round(projected),
                      round(margin), verdict, p)
