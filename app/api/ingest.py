"""文档入库：支持 multipart 文件上传（前端用）+ 服务端路径（运维/调试）。

上传的文件落盘到 rag/data/uploads/，再走 rag_main.ingest 单文件模式。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services import rag

router = APIRouter()

# 支持的扩展名（与 rag/main.py SUPPORTED_EXTS 对齐）
SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".markdown", ".docx", ".html", ".jpg", ".png"}


@router.post("/ingest")
async def ingest(files: Annotated[list[UploadFile], File(description="支持的文档: pdf/txt/md/docx/html/图片")]) -> dict:
    rag.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    errors: list[dict] = []
    for up in files:
        name = Path(up.filename or "unnamed").name          # 防路径穿越：只取文件名
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            errors.append({"name": name, "reason": f"不支持的格式 {ext}（支持: {sorted(SUPPORTED_EXTS)}）"})
            continue
        dest = rag.UPLOAD_DIR / name
        try:
            content = await up.read()
            dest.write_bytes(content)
            saved.append(dest)
        except Exception as e:  # noqa: BLE001
            errors.append({"name": name, "reason": f"文件写入失败: {e}"})

    if not saved:
        raise HTTPException(status_code=400, detail={"errors": errors})

    stats = rag.ingest_files(saved)
    stats["errors"] = errors
    return stats