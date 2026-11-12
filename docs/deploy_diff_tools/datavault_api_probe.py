"""DataVault 服务端能力探测（只读）

列出运行中的 DataVault 服务暴露的全部 setting，用来判断"复制一个 session"
有没有服务端原生做法（而不是逐个数据集重写）。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\datavault_api_probe.py

只读：只枚举 setting 列表，不调用任何写操作。
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

# 只要出现这些词，就说明可能有原生复制/搬运能力
INTERESTING = ("copy", "clone", "dup", "move", "rename", "delete", "new", "add", "import", "export")


def main() -> int:
    from services.common.config import labrad_connect

    cxn = labrad_connect()
    dv = cxn.data_vault
    print(f"connected, ID={getattr(cxn, 'ID', '?')}")

    try:
        settings = list(dv.settings)
    except Exception as exc:  # noqa: BLE001
        print(f"dv.settings not available: {exc}")
        settings = []

    print(f"\nDataVault settings: {len(settings)} 个")
    for s in settings:
        name = getattr(s, "name", None) or str(s)
        doc = (getattr(s, "doc", "") or "").strip().splitlines()
        first = doc[0] if doc else ""
        flag = "  <== 关注" if any(k in name.lower() for k in INTERESTING) else ""
        print(f"  {name:<24} {first[:70]}{flag}")

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
