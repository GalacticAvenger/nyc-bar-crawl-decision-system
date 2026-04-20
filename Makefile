.PHONY: test demo app lint clean enrich audit

PY := python3

test:
	$(PY) -m pytest tests/ -v

enrich:
	$(PY) scripts/enrich_bars.py

audit:
	$(PY) scripts/neighborhood_audit.py

demo:
	$(PY) -m jupyter notebook notebooks/demo.ipynb

app:
	$(PY) -m streamlit run app/streamlit_app.py

lint:
	$(PY) -m pyflakes src/ tests/ scripts/ || true

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov/ 2>/dev/null || true
