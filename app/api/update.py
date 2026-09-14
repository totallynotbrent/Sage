from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

logger = logging.getLogger("app")


class UpdateResult(BaseModel):
    message: str
    updated: bool


def _repo_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent.parent
    if (root / ".git").exists():
        return root
    return None


def _pull(root: Path) -> tuple[str, str]:
    before = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(root), "fetch", "origin", "main"],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "reset", "--hard", "origin/main"],
        capture_output=True,
        text=True,
    )
    after = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return before, after


@router.post("/api/update", response_model=UpdateResult)
async def update():
    root = _repo_root()
    if root is None:
        return {"message": "no git repo found", "updated": False}

    before, after = await asyncio.to_thread(_pull, root)

    if before != after:
        asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))
        return {"message": "updated, restarting", "updated": True}

    return {"message": "already up to date", "updated": False}