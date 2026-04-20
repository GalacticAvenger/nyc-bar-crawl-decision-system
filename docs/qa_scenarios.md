# Manual QA Scenarios

Ten human-graded scenarios. Each is runnable via `notebooks/demo.ipynb` or the test suite.

| # | Name | Inputs (abbr.) | Expected | Rubric |
|---|---|---|---|---|
| 1 | Aligned trio | 3 users, vibes cluster on "intimate + conversation" | Route found; utilitarian or copeland strategy; ≥1 user note surfaced if applicable | Pass if: route produced, strategy is plausible, explanation names ≥1 bar + ≥1 user + ≥1 attribute |
| 2 | Wide budget gap | Student ($6) + Banker ($25) | `egalitarian_min` strategy fires | Explanation names the poorest member and cites the 3×+ spread |
| 3 | Heavy vetoes | User vetoes top-35 expensive bars | `approval_veto` fires; none of the vetoed bars appear in the route | Strategy == "approval_veto"; route ∩ vetoed == ∅; explanation names the vetoer |
| 4 | Infeasible window | 10-minute window | Empty route; graceful explanation; no crash | `route.is_empty` true; explanation is non-empty |
| 5 | Flat preferences | All vibes weighted equally | Route produced, fallback to novelty + quality_signal | No crash; route non-empty if time allows |
| 6 | Single-neighborhood filter | `neighborhoods=("Astoria",)` | All stops in Astoria | Every stop's neighborhood is "Astoria" |
| 7 | Burp-Castle-in-route | Group near EV, prefers quiet + conversation | Burp Castle appears; `user_note` ("whispering") surfaces in text | Explanation contains "whisper" |
| 8 | Deterministic re-run | Same inputs, two runs | Byte-identical output | Route ids, strategy, all counterfactuals match |
| 9 | CBR retrieval | Intimate LES 2-person date | `case_les_speakeasy_ladder` retrieved #1 | Top case id matches |
| 10 | Strategy counterfactuals | Any multi-user group | All 5 strategies return a winner | `strategy_cf_winners` has 5 non-None entries |

## How to run

The integration tests (`tests/test_end_to_end.py`) cover scenarios 1–8 with hard assertions. Scenarios 9–10 are validated in `tests/test_case_based.py` and `tests/test_option_generation.py`. The notebook (`notebooks/demo.ipynb`, §9) demonstrates scenarios 3–5 visually.

Every scenario passes as of Spring 2026.
