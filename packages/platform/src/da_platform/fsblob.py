"""文件系统 BlobStore（10.2 POSIX 兜底驱动，单机模式/私有化-单机形态）。

fencing token 条件写：sidecar .token 文件记录已见最大 token，旧 token 写入拒绝。
生产 S3 形态用对象元数据 + 条件写实现同一语义（同一套一致性测试）。
"""

from __future__ import annotations

from pathlib import Path

from da_platform.primitives import StaleTokenError


class FileSystemBlobStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root.resolve())):
            raise ValueError(f"blob key 越界: {key}")
        return p

    async def put(self, key: str, data: bytes, fencing_token: int | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if fencing_token is not None:
            token_file = path.with_suffix(path.suffix + ".token")
            recorded = int(token_file.read_text()) if token_file.exists() else -1
            if fencing_token < recorded:
                raise StaleTokenError(
                    f"stale fencing token {fencing_token} < {recorded} for {key}"
                )
            token_file.write_text(str(fencing_token))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # 原子替换

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    async def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".token").unlink(missing_ok=True)

    async def list_keys(self, prefix: str) -> list[str]:
        keys = []
        for p in self._root.rglob("*"):
            if p.is_file() and not p.name.endswith((".token", ".tmp")):
                key = str(p.relative_to(self._root))
                if key.startswith(prefix):
                    keys.append(key)
        return sorted(keys)
