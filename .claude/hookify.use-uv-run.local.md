---
name: use-uv-run
enabled: true
event: bash
pattern: ^python3?\s+|^python3?\s*$
action: block
---

**Use `uv run` instead of direct python commands**

Direct `python` or `python3` commands bypass the project's virtual environment.

**Instead of:**
```
python script.py
python -m module
python3 -c "code"
```

**Use:**
```
uv run python script.py
uv run python -m module
uv run python -c "code"
```

Or for CLI tools defined in pyproject.toml:
```
uv run ocaptain --help
```
