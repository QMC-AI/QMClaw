"""探测 DataLab 载入最新数据集后暴露的编号属性，用于 /plot 的 {exp_num} 替换。"""
import os
import sys

SERVER = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                      "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER)

from services.common.config import labrad_connect  # noqa: E402

SP = ["", "LQHL", "test", "20251103_LD_SQ"]


def main():
    cxn = labrad_connect()
    dv = cxn.data_vault
    from lqms.data_process import dataAnalysisCore as dc
    data = dc.DataLab(SP, dv, dv_type="data_vault")
    data.loadDataset(-1)
    print("dataset_name =", getattr(data, "dataset_name", "?"), flush=True)
    for a in ["dataset_num", "exp_num", "_exp_num", "num", "index", "dataset_id",
              "current_dataset", "datasets_num", "data_path", "name"]:
        try:
            print(f"{a} = {getattr(data, a)!r}", flush=True)
        except Exception as e:
            print(f"{a} ERR {type(e).__name__}: {e}", flush=True)
    print("data.shape =", data.data.shape, flush=True)
    print("dict-ish:", {k: v for k, v in getattr(data, "__dict__", {}).items()
                        if isinstance(v, (int, str))} if hasattr(data, "__dict__") else "n/a", flush=True)
    cxn.disconnect()


if __name__ == "__main__":
    main()
