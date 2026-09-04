# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260904-001] best_practice

**Logged**: 2026-09-04T00:00:00+08:00
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
Run source-only compile checks instead of recursively compiling virtual environments.

### Details
Recursive compilation of a repository directory can descend into its local virtual environment and take unnecessarily long. Targeted source files provide the same syntax signal with predictable runtime.

### Suggested Action
Keep validation commands scoped to application and test source paths.

### Metadata
- Source: error
- Related Files: rag/.venv
- Tags: validation, python

---
