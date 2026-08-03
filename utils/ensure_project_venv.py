"""프로젝트 venv로 재실행 (stdlib only).

스토어/시스템 python 으로 배치를 돌리면 sqlalchemy 등이 없어
ModuleNotFoundError 가 난다. 이 헬퍼는 같은 스크립트를
venv\\Scripts\\python.exe 로 다시 띄운다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional


def ensure_project_venv(*, marker: str = "sqlalchemy") -> None:
    try:
        __import__(marker)
        return
    except ImportError:
        pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root, "venv", "Scripts", "python.exe"),
        os.path.join(root, ".venv", "Scripts", "python.exe"),
        os.path.join(root, "venv", "bin", "python"),
        os.path.join(root, ".venv", "bin", "python"),
    ]
    here = os.path.normcase(os.path.abspath(sys.executable))
    script = os.path.abspath(sys.argv[0])
    args = list(sys.argv[1:])

    for cand in candidates:
        if not os.path.isfile(cand):
            continue
        if os.path.normcase(os.path.abspath(cand)) == here:
            continue
        print(
            f"[venv] {marker} 없음 ({sys.executable}) → {cand} 로 재실행",
            file=sys.stderr,
        )
        raise SystemExit(subprocess.call([cand, script, *args]))

    print(
        f"{marker} 모듈이 없습니다. 프로젝트 venv로 실행하세요.\n"
        f"  .\\venv\\Scripts\\python.exe {os.path.basename(script)} {' '.join(args)}".rstrip(),
        file=sys.stderr,
    )
    raise SystemExit(1)
