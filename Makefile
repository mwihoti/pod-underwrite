PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: venv check run seed audit ten deploy clean

venv:
	python3 -m venv .venv
	$(PIP) -q install -e ".[dev]"

check: venv
	$(PY) -m pytest
	$(PY) -m compileall -q podx

run: venv
	$(PY) -m uvicorn podx.web:app --host 0.0.0.0 --port 8000

seed:
	$(PY) -m scripts.audit_many --samples

ten:
	$(PY) -m scripts.audit_many --top 10

deploy:
	docker compose up -d --build

clean:
	rm -rf .venv __pycache__ podx/__pycache__ tests/__pycache__ data
