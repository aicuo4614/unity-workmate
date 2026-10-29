"""Checkpoint 基础设施：对外只暴露 init_checkpoint / get_checkpointer / close_checkpoint。

编排（graph_manager）禁止直接 new InMemorySaver/SqliteSaver；换后端只改这里。
为何用 AsyncSqliteSaver：图跑在 astream/ainvoke 上，同步 SqliteSaver.aget_* 会直接 NotImplementedError。
"""
from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

# 默认落在仓库 server/data/
_DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "checkpoints.sqlite"

_checkpointer = None
_sqlite_conn = None


async def init_checkpoint(*, use_sqlite: bool = True, db_path: Path | None = None) -> None:
    """进程启动时调用一次。Sqlite 失败则回退内存，避免拖死 API。"""
    global _checkpointer, _sqlite_conn
    await close_checkpoint()
    if use_sqlite:
        path = db_path or _DEFAULT_DB
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            # 连接挂在 lifespan 上；不要用 from_conn_string（退出 with 就关库）
            _sqlite_conn = await aiosqlite.connect(str(path))
            _checkpointer = AsyncSqliteSaver(_sqlite_conn)
            await _checkpointer.setup()
            print(f"Checkpoint: AsyncSqlite → {path}")
            return
        except Exception as e:
            print(f"Checkpoint: Sqlite 失败，回退 InMemory — {type(e).__name__}: {e}")
            if _sqlite_conn is not None:
                try:
                    await _sqlite_conn.close()
                except Exception:
                    pass
                _sqlite_conn = None
    _checkpointer = InMemorySaver()
    print("Checkpoint: InMemorySaver")


async def close_checkpoint() -> None:
    """进程退出时关库，避免 aiosqlite 连接泄漏。"""
    global _checkpointer, _sqlite_conn
    conn = _sqlite_conn
    _sqlite_conn = None
    _checkpointer = None
    if conn is not None:
        await conn.close()


def get_checkpointer():
    if _checkpointer is None:
        raise RuntimeError("checkpoint 未初始化，请先 await init_checkpoint()")
    return _checkpointer
