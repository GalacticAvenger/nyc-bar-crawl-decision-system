# Rules for Claude Code on this repo

1. **Never remove a test.** If you change a test, add a comment explaining what changed and why. If you think a test is wrong, flag it and ask — do not silently edit.
2. **After every phase, run pytest and the full eval harness** (`evaluation/eval_harness.py` and `evaluation/eval_deep.py`). Commit only when both are green.
3. **No hardcoded magic numbers in `src/`.** Every threshold goes in `data/rules.yaml` under a clearly named key.
4. **No free-form LLM calls in runtime code.** The system is symbolic.
5. **When adding a feature, write the tests first.** Then the implementation. Then run tests. Then commit.
6. **If a phase's instructions are ambiguous, stop and ask before assuming** — especially around explanation prose style.
7. **Preserve the existing module boundaries.** Do not collapse `scoring.py` and `group_aggregation.py` into one file even if it seems tidier.
8. **Prose in explanations must never contain literal template variables.** A test must catch this.
