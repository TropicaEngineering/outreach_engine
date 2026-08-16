.PHONY: demo demo-universal live test lint serve serve-demo reset-demo

demo:
	PYTHONPATH=src python3 -m outreach_engine.cli demo --reset

demo-universal:
	PYTHONPATH=src python3 -m outreach_engine.cli demo --reset --playbook universal --fixture fixtures/universal_signals.json

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

lint:
	python3 -m ruff check src tests

live:
	.venv/bin/signal-route ingest-gmail --provider openai --limit 5

serve:
	.venv/bin/signal-route serve

serve-demo:
	OUTREACH_ARTIFACT_PATH=./data/demo_artifacts PYTHONPATH=src .venv/bin/python -m outreach_engine.cli --database data/demo.sqlite3 serve
reset-demo:
	cp data/demo_seed.sqlite3 data/demo.sqlite3