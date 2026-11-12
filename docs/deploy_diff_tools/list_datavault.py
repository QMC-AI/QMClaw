"""DataVault 目录只读浏览 — 用来挑一个真实存在的 session 路径

只做 `cd` / `dir` / `ls`，不新建、不删除、不打开数据集。连接到 config/system.json
里配置的 LabRAD（可用 LABRADHOST / LABRADPORT 覆盖）。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\list_datavault.py [层数]

输出形如：
    /                       ['LQHL', 'test', ...]
    /LQHL                   ['test', ...]
    /LQHL/test              ['20260324', ...]

把找到的路径去掉第一层用户名，填进 qmclaw-server/config/session.json 的 path
（user 单独填），例如 /LQHL/test/20260324 → user=LQHL, path=["test","20260324"]。
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

MAX_LEVELS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
MAX_CHILDREN = 25


def main() -> int:
    from services.common.config import get_labrad_params, labrad_connect

    params = get_labrad_params()
    print(f"connecting to {params['host']}:{params['port']} (timeout {params['timeout']}s) ...")
    cxn = labrad_connect()
    print(f"connected, ID={getattr(cxn, 'ID', '?')}")
    dv = cxn.data_vault

    def walk(path, level):
        try:
            # DataVault 的 cd 是相对当前目录的；先 cd('') 回根，再按绝对路径逐层进
            dv.cd("")
            if path:
                dv.cd(path)
        except Exception as exc:  # noqa: BLE001
            print(f"{'  ' * level}/{'/'.join(path)}  -> cd failed: {str(exc)[:120]}")
            return
        try:
            # data_vault.dir() 返回 [子目录列表, 数据集列表] 两段结构
            raw = dv.dir()
            subdirs = list(raw[0]) if len(raw) >= 1 else []
            datasets = list(raw[1]) if len(raw) >= 2 else []
        except Exception as exc:  # noqa: BLE001
            print(f"{'  ' * level}/{'/'.join(path)}  -> dir failed: {exc}")
            return
        shown = [str(x) for x in subdirs[:MAX_CHILDREN]]
        more = "" if len(subdirs) <= MAX_CHILDREN else f"  (+{len(subdirs) - MAX_CHILDREN} more)"
        print(
            f"{'  ' * level}/{'/'.join(str(p) for p in path):<30} "
            f"dirs={shown}{more}  datasets={len(datasets)}"
        )
        if level + 1 < MAX_LEVELS:
            for name in subdirs:
                walk(path + [str(name)], level + 1)

    walk([], 0)
    try:
        cxn.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        sys.exit(2)
