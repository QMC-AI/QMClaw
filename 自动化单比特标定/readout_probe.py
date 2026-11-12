# -*- coding: utf-8 -*-
"""只读读出排查：s21 宽扫 / s21_power2d 功率扫描。全程 update false，不写寄存器。"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:3008"
BIT = os.environ.get("BIT", "q7ld3")   # 换比特：set BIT=q8ld3
SESSION = "20251103_LD_SQ"


def post(path, body, timeout=1800):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def parse_validate(text):
    ir = post("/workflows/parse_qwf", {"text": text})
    if not ir.get("ok"):
        return None, ir.get("error") or ir
    val = post("/workflows/validate", {"ir": ir["ir"]})
    if not val.get("ok"):
        return None, val.get("error") or val
    return ir["ir"], None


def run_ir(ir, dry=False, timeout=2400, poll=5.0):
    wid = f"wf_readout_{int(time.time())}"
    launch = post("/workflows/run_ir", {"ir": ir,
                                        "context": {"qubit": BIT, "session": SESSION},
                                        "workflowId": wid, "dryRun": dry})
    if not launch.get("ok"):
        return {"status": "launch_failed", "error": launch.get("error")}
    if launch.get("status") == "queued":
        print("    queued, position", launch.get("queuePosition"), flush=True)
    t0 = time.time()
    while True:
        time.sleep(poll)
        st = post("/workflows/status", {"workflowId": wid})
        state = st.get("status")
        if state in ("completed", "failed", "cancelled", "budget_exceeded",
                     "needs_human", "quota_exceeded"):
            st["elapsed"] = round(time.time() - t0, 1)
            nodes = st.get("nodes") or {}
            if isinstance(nodes, list):
                nodes = {str(n.get("id")): n for n in nodes}
            slim = {}
            for nid, n in nodes.items():
                res = n.get("result") or {}
                slim[nid] = {"status": n.get("status"),
                             "dataset": n.get("dataset_id") or res.get("dataset_id"),
                             "error": n.get("error")}
            st["node_map"] = slim
            return st
        if time.time() - t0 > timeout:
            post("/workflows/cancel", {"workflowId": wid})
            return {"status": "timeout", "workflowId": wid}


def qwf_s21(f0, f1, step):
    return f"""workflow readout_s21_broad {{
  meta {{ name "readout_s21_broad"  description "读出排查：只读 s21 宽扫"  tags [readout, s21] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 1800  max_writes 1  max_datasets 1  max_scan_points 800 }}
  goal {{ objective "拿到读出腔 S21 曲线"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.s21  qubit $env.qubit  timeout 1200  note "s21"  args {{ freq range {f0} {f1} {step}  update false }} }}
  say "dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""


def qwf_s21_default():
    return f"""workflow readout_s21_default {{
  meta {{ name "readout_s21_default"  description "读出排查：fread 附近默认 s21"  tags [readout, s21] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 900  max_writes 1  max_datasets 1  max_scan_points 100 }}
  goal {{ objective "找读出腔精确频率"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.s21  qubit $env.qubit  timeout 600  note "s21 default around fread"  args {{ update false }} }}
  say "dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""


def qwf_spec(f0, f1, step, amp):
    return f"""workflow readout_spec {{
  meta {{ name "readout_spec"  description "读出修好后重找 f10"  tags [spec] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 1800  max_writes 1  max_datasets 1  max_scan_points 600 }}
  goal {{ objective "找到比特谱线"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.spectroscopy  qubit $env.qubit  timeout 1200  note "spec"  args {{ freq range {f0} {f1} {step}  spec_amp {amp}  update false }} }}
  say "dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""


def qwf_power(f0, f1, step, p0, p1, pstep):
    return f"""workflow readout_power2d {{
  meta {{ name "readout_power2d"  description "读出排查：功率扫描"  tags [readout, power2d] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 3000  max_writes 1  max_datasets 1  max_scan_points 800 }}
  goal {{ objective "找到读出最佳功率"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.s21_power2d  qubit $env.qubit  timeout 2400  note "power2d"  args {{ freq range {f0} {f1} {step}  power range {p0} {p1} {pstep}  update false }} }}
  say "dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "s21"
    if mode == "spec":
        f0, f1, step, amp = sys.argv[2:6]
        text = qwf_spec(f0, f1, step, amp)
        tag = "spec"
    elif mode == "default":
        text = qwf_s21_default()
        tag = "s21_default"
    elif mode == "s21":
        if len(sys.argv) >= 5:
            f0, f1, step = sys.argv[2:5]
            text = qwf_s21(f0, f1, step)
        else:
            text = qwf_s21(6.55, 6.90, 0.002)
        tag = "s21"
    else:
        f0, f1, step, p0, p1, pstep = sys.argv[2:8]
        text = qwf_power(f0, f1, step, p0, p1, pstep)
        tag = "power"
    ir, err = parse_validate(text)
    if err:
        print("PARSE/VALIDATE FAIL:", json.dumps(err, ensure_ascii=False)[:800])
        return 1
    dry = run_ir(ir, dry=True, timeout=300)
    print("dryRun:", dry.get("status"), json.dumps(dry.get("node_map") or {}, ensure_ascii=False)[:200], flush=True)
    st = run_ir(ir, dry=False)
    print("run:", st.get("status"), "outcome:", json.dumps(st.get("outcome"), ensure_ascii=False)[:200])
    print("nodes:", json.dumps(st.get("node_map"), ensure_ascii=False)[:400])
    json.dump(st, open(f"D:/qmclaw/_readout_{tag}_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
