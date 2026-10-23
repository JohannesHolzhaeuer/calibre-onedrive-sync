## Summary

<!-- One paragraph describing the change. -->

## Checklist

- [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
- [ ] `ruff check src tests build build.py` passes.
- [ ] `ruff format --check src tests build build.py` passes.
- [ ] `pytest --cov` passes (coverage floor is 90 %).
- [ ] `python build.py` passes (ZIP ROOT contains `__init__.py` + the import-name marker).
- [ ] `python build/sync_version.py --check` passes.
- [ ] User-facing strings are marked with `_()`.
- [ ] `README.md` / `AGENTS.md` updated when behaviour or layout changed.
