PYTHON ?= python3
export PYTHONPATH := src

.PHONY: test demo demo-doc diagrams clean help

help:
	@echo "make test      run the test suite"
	@echo "make demo      run the scripted demonstration"
	@echo "make demo-doc  re-capture the demo output into docs/demo-output.md"
	@echo "make diagrams  re-render the Mermaid diagrams (needs mermaid-cli: mmdc)"

test:
	$(PYTHON) -m pytest -q

demo:
	$(PYTHON) -m mini_middleware demo

demo-doc:
	@{ echo '# Demo output'; echo; \
	   echo 'Real output from `make demo`, captured on '"$$(date -u +%Y-%m-%d)"' with Python '"$$($(PYTHON) -c 'import platform;print(platform.python_version())')"'.'; \
	   echo 'Timestamps and correlation ids (where not set explicitly) differ on every run. The random suffix of the temporary directory name is replaced with XXXX; nothing else is edited.'; \
	   echo 'Log lines are JSON, one per line. The scripted scenarios are described in the headings.'; \
	   echo; echo '```text'; $(PYTHON) -m mini_middleware demo 2>&1 | sed 's#/tmp/mm-demo-[A-Za-z0-9_]*#/tmp/mm-demo-XXXX#'; echo '```'; } > docs/demo-output.md
	@echo "wrote docs/demo-output.md"

# Optional: MMDC_ARGS="-p puppeteer.json" if Chromium needs --no-sandbox (for example in containers)
diagrams:
	for f in docs/diagrams/*.mmd; do mmdc $(MMDC_ARGS) -i $$f -o $${f%.mmd}.png -s 2 -b white; done

clean:
	rm -rf .pytest_cache data build *.egg-info src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
