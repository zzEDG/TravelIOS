"""并发执行独立的 I/O；出错或取消时先收回同组任务，再释放请求资源。"""

import asyncio
from collections.abc import Coroutine
from typing import Any


async def gather_results(*operations: Coroutine[Any, Any, Any]) -> list[Any]:
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # 不把尚在使用 AsyncClient 的子任务留到请求退出后继续运行。
        await asyncio.gather(*tasks, return_exceptions=True)
