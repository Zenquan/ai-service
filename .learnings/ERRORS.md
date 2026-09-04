# Errors

Command failures and integration errors.

---

## [ERR-20260904-001] eval_command

**Logged**: 2026-09-04T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
The real offline evaluation command could not run because the local Qdrant index was absent.

### Error
```
qdrant_data absent
zsh: no such file or directory: rag/.venv/bin/python3.12
```

### Context
- Attempted to run the evaluation from the `rag` directory.
- The command used a repository-root-relative virtualenv path while already inside `rag`.
- This checkout also has no `rag/qdrant_data`, so no indexed corpus is available for real Recall@K/MRR values.

### Suggested Fix
Run `./.venv/bin/python3.12 main.py eval` after ingesting the corpus, or run the root-relative command from the repository root.

### Metadata
- Reproducible: yes
- Related Files: rag/main.py, rag/qdrant_data

### Resolution
- **Resolved**: 2026-09-04T00:00:00+08:00
- **Notes**: Validated the evaluator through offline unit tests and validated source syntax with targeted compilation.

---
