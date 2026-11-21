"""
并行计算支持。

两期三角网叠加、跨边界三角形裁剪、逐三角形方量计算这类纯 Python 循环，按连续区段分块交给
进程池（multiprocessing，spawn 方式），结果按原顺序拼回，与串行计算逐位一致。

未使用 dask：实测它的 processes 调度器每次计算都新建进程池、threads 调度器受 GIL 限制，
在这些循环上都不比串行快；方格网取样（find_simplex）按网格顺序查询本身只需几十毫秒。

数据量小于阈值时直接串行，避免进程启动和数据传输的开销反而更慢。
进程池出现任何问题（打包环境受限、权限、子进程崩溃）都会自动退回串行，不影响计算结果。

环境变量 TIN_EARTHWORK_WORKERS：并行进程数；设为 1 关闭多进程。
打包程序须在入口调用 multiprocessing.freeze_support()（见 main.py）。
"""
import atexit
import contextlib
import gc
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, List, Optional, Sequence, TypeVar

import numpy as np

T = TypeVar("T")
R = TypeVar("R")

logger = logging.getLogger(__name__)

WORKERS_ENV = "TIN_EARTHWORK_WORKERS"
# 进程数上限：再多受内存带宽和数据传输限制，收益很小
MAX_WORKERS = 8
# 每个进程分到的块数；略多于 1 便于负载均衡
CHUNKS_PER_WORKER = 2
# 三角形数低于该值时串行（进程间传输与调度开销大于收益）
MIN_PARALLEL_TRIANGLES = 20_000
# 跨边界、需要裁剪的三角形数低于该值时串行
MIN_PARALLEL_CLIPS = 2_000

_executor: Optional[ProcessPoolExecutor] = None
_executor_lock = threading.Lock()
_process_disabled = False


_gc_lock = threading.Lock()
_gc_depth = 0
_gc_was_enabled = False


@contextlib.contextmanager
def gc_paused():
    """批量创建大量对象（三角形、测点、多边形）时暂停循环垃圾回收。

    这些对象不构成引用环，暂停期间内存照常按引用计数释放；数十万个对象时
    反复触发的全代回收能占到一半以上的时间。可嵌套、可多线程同时使用。
    """
    global _gc_depth, _gc_was_enabled
    with _gc_lock:
        if _gc_depth == 0:
            _gc_was_enabled = gc.isenabled()
            gc.disable()
        _gc_depth += 1
    try:
        yield
    finally:
        with _gc_lock:
            _gc_depth -= 1
            if _gc_depth == 0 and _gc_was_enabled:
                gc.enable()


def worker_count() -> int:
    """可用的并行进程数；环境变量可覆盖，非法值按 1 处理。"""
    value = os.environ.get(WORKERS_ENV, "").strip()
    if value:
        try:
            return max(1, int(value))
        except ValueError:
            return 1
    return max(1, min(os.cpu_count() or 1, MAX_WORKERS))


def chunk_bounds(count: int, chunks: int) -> List[range]:
    """把 [0, count) 分成至多 chunks 个连续区段。"""
    chunks = max(1, min(chunks, count))
    edges = np.linspace(0, count, chunks + 1).round().astype(int).tolist()
    return [range(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def _get_executor(workers: int) -> ProcessPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            # 统一用 spawn：GUI 在后台线程中计算，fork 带线程的进程不安全；Windows/macOS 也默认 spawn
            context = multiprocessing.get_context("spawn")
            _executor = ProcessPoolExecutor(max_workers=workers, mp_context=context)
        return _executor


def shutdown():
    """关闭进程池（程序退出时调用；之后需要时会重新创建）。"""
    global _executor
    with _executor_lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


atexit.register(shutdown)


def use_processes(items: int, threshold: int = MIN_PARALLEL_TRIANGLES) -> bool:
    return not _process_disabled and items >= threshold and worker_count() > 1


def process_map(func: Callable[[T], R], tasks: Sequence[T]) -> List[R]:
    """按顺序返回 [func(task) ...]；多进程失败时退回串行。func 须为模块级函数。"""
    global _process_disabled
    if len(tasks) > 1 and not _process_disabled:
        try:
            return list(_get_executor(worker_count()).map(func, tasks))
        except Exception as error:  # noqa: BLE001 —— 任何进程池故障都退回串行，结果不受影响
            logger.warning("多进程计算失败，改为串行：%s", error)
            _process_disabled = True
            shutdown()
    return [func(task) for task in tasks]


def process_chunks(count: int) -> List[range]:
    return chunk_bounds(count, worker_count() * CHUNKS_PER_WORKER)
