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

## [LRN-20260906-001] best_practice

**Logged**: 2026-09-06T16:40:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
目录重命名后，Python editable 安装里的 `.pth` 会残留旧绝对路径，导致 uvicorn reload/spawn 子进程找不到包。

### Details
`uv pip install -e` 生成的 `_editable_impl_<pkg>.pth` 写的是安装时的绝对源码路径。
仓库文件夹改名（`fastapi-app` → `ai-service`）后，pth 仍指向旧路径；普通单进程或 pytest
（`pythonpath=src`）可能不暴露，而 `--reload`/`--workers N` 的 spawn 子进程稳定触发
`ModuleNotFoundError`。

### Suggested Action
- 移动/重命名项目目录后检查 `server/.venv/lib/python3.12/site-packages/_editable_impl_*.pth`。
- 规范修法：`cd server && uv pip install --python .venv/bin/python -e '.[local]' --group dev`。
- 验证：用 `multiprocessing` spawn 子进程导入包，或直接跑一次带 `--reload` 的 uvicorn。

### Metadata
- Source: error
- Related Files: server/.venv/lib/python3.12/site-packages/_editable_impl_server.pth
- Tags: python, venv, editable-install, rename, uvicorn
- See Also: ERR-20260906-001
- Pattern-Key: env.venv_editable_pth_after_rename

### Resolution
- **Resolved**: 2026-09-06T16:40:00+08:00
- **Notes**: 已修正 pth 路径并通过 spawn 子进程导入验证。

---
