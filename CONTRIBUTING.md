# Contributing

Thank you for taking an interest. This is a small educational reference, so the bar for changes is clarity and correctness rather than feature count.

## Ground rules

* **Keep it independent and generic.** Do not add real CargoWise schemas, endpoints, credentials, customer data, screenshots or text copied from proprietary documentation. Message shapes must stay illustrative. If a contribution would only make sense with proprietary material, it does not belong here.
* **Standard library only** in `src/`. Test-only extras (pytest, lxml) are fine.
* **No claims you cannot back.** No performance figures, production-use statements or compatibility promises without evidence in the repository.
* **British English**, plain professional tone, no hype.

## Setting up

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install pytest lxml        # lxml is optional; it enables the XSD cross-check test
make test
make demo
```

Python 3.11 or newer is required.

## Making a change

1. Open an issue or describe the change in the pull request.
2. Add or update tests. A behaviour change without a test will be asked for one.
3. If the change alters a design decision, add an ADR in `docs/adr/` (MADR format) and a row in `docs/adr/README.md`, or supersede an existing one.
4. If you change a diagram, edit the `.mmd` source in `docs/diagrams/`, re-render with `make diagrams` (needs `mmdc` from mermaid-cli), and update the Mermaid block in `docs/architecture.md` to match.
5. If the demo output changes, run `make demo-doc` to refresh `docs/demo-output.md`.
6. Run `make test` and make sure it passes on at least one supported Python version. CI runs 3.11, 3.12 and 3.13.

## Reporting problems

Open an issue with what you expected, what happened, and the smallest example that shows it. For anything that looks like a security problem in the reference code, say so in the issue title without posting exploit details, and the maintainer will arrange a private channel.
