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

## [ERR-20260906-001] uvicorn_spawn_module_not_found

**Logged**: 2026-09-06T16:40:00+08:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
uvicorn 子进程（`--reload` / 多 worker spawn）报 `ModuleNotFoundError: No module named 'server'`。

### Error
```
Process SpawnProcess-1:
...
ModuleNotFoundError: No module named 'server'
```

### Context
- 操作：`./scripts/start.sh`（内部 `uvicorn server.main:app --reload`）或带 `--workers N` 启动。
- 根因：目录从 `fastapi-app` 重命名为 `ai-service` 后，
  `server/.venv/lib/python3.12/site-packages/_editable_impl_server.pth`
  仍写死旧绝对路径 `/Users/zenquan/ZCodeProject/fastapi-app/server/src`。
  直接 `import server` 时若恰好在 server/ 下或 pytest 配了 `pythonpath=src` 可掩盖，
  但 spawn 出来的子进程找不到包。

### Suggested Fix
把 pth 改为当前路径，或重新生成 editable 安装：
```bash
cd server
uv pip install --python .venv/bin/python -e '.[local]' --group dev
```

### Metadata
- Reproducible: yes
- Related Files: server/.venv/lib/python3.12/site-packages/_editable_impl_server.pth
- See Also: LRN-20260906-001

### Resolution
- **Resolved**: 2026-09-06T16:40:00+08:00
- **Notes**: 已把 pth 指向 `/Users/zenquan/ZCodeProject/ai-service/server/src`，
  并用 spawn 子进程导入 `server.main` 实测通过。

---
