"""
services/common/plotting.py - matplotlib pyplot 串行化

matplotlib 的 pyplot 是**进程级全局**状态：所有 figure 都挂在同一份全局注册表里，
plt.close('all') / plt.get_fignums() / plt.figure(num) 操作的正是这份全局状态。
服务改用多线程 HTTP 服务器后，两个并发绘图请求会互相拆台：A 刚建好 figure
还没 savefig，B 的 plt.close('all') 就把它关掉了（图片空白，或拿到别人的 figure）。

所以任何触碰 pyplot 全局状态的代码段都必须先拿到 PLOT_LOCK，
保证同一时刻进程内只有一个绘图区段在跑。

用法：
    from ..common.plotting import PLOT_LOCK, plot_lock, serialized_plot

    @serialized_plot
    def _handle_plot(self, data): ...

    with plot_lock():
        plt.close('all')
        ...

锁是可重入的 RLock：绘图路径内部互相调用（_handle_plot_offline_v2 →
_execute_plot_command → qter.fitData）不会自己锁死自己。
"""

import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

# 进程级 pyplot 串行锁。所有 pyplot 全局操作（close / get_fignums / figure /
# subplots / rcParams / savefig）都必须持有它。
PLOT_LOCK = threading.RLock()

_F = TypeVar("_F", bound=Callable[..., Any])


@contextmanager
def plot_lock() -> Iterator[threading.RLock]:
    """进入 with 块期间独占进程内的 pyplot 全局状态，退出时释放。"""
    with PLOT_LOCK:
        yield PLOT_LOCK


def serialized_plot(func: _F) -> _F:
    """装饰器：被装饰函数整个执行期间持有 PLOT_LOCK。

    适合"取数据 → 画图 → 保存"一体的绘图入口；只画一小段的函数
    建议改用 `with plot_lock():` 把 pyplot 区段单独圈出来。
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        with PLOT_LOCK:
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
