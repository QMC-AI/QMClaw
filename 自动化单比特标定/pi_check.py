# -*- coding: utf-8 -*-
"""q8ld3 π 脉冲验证：精扫定 f10 → 新频率下 piamp(0..amp) + iqraw。

用法（先设环境变量 BIT=q8ld3）：
  python pi_check.py set bias_z=-2.8
  python pi_check.py spec 3.19 3.23 0.0005 0.1
  python pi_check.py set f10=3.2093 f21=3.0093
  python pi_check.py pi 3.0 3.0
全部 update false；寄存器写入由 set 子命令显式完成并回读校验。
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from readout_probe import BIT, SESSION, parse_validate, post, run_ir  # noqa: E402

Q = "http://127.0.0.1:3003"


def qpost(path, body, timeout=120):
    req = urllib.request.Request(Q + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def qget(path):
    with urllib.request.urlopen(Q + path, timeout=60) as r:
        return json.loads(r.read())


def reg():
    return qget("/qubit/registry?name=" + BIT)["registry"]


def flat(d, pre=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, pre + k + "."))
        else:
            out[pre + k] = v
    return out


def cmd_set(pairs):
    params = {}
    for p in pairs:
        k, v = p.split("=", 1)
        params[k] = float(v)
    print("write:", json.dumps(params, ensure_ascii=False))
    r = qpost("/qubit/set_params", {"name": BIT, "params": params})
    print("resp:", json.dumps(r, ensure_ascii=False)[:400])
    time.sleep(0.6)
    f = flat(reg())
    bad = [(k, v, f.get(k)) for k, v in params.items() if abs(float(f.get(k, 1e9)) - v) > 1e-6]
    print("readback:", json.dumps({k: f.get(k) for k in params}, ensure_ascii=False),
          "MISMATCH", bad or "none")
    return 0 if not bad else 1


def qwf_spec(f0, f1, step, amp):
    n = int(round((float(f1) - float(f0)) / float(step))) + 1
    return f"""workflow q8_spec_fine {{
  meta {{ name "q8_spec_fine"  description "q8ld3 精扫定 f10"  tags [spec] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 1800  max_writes 1  max_datasets 1  max_scan_points {n + 20} }}
  goal {{ objective "精扫确认比特谱线"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.spectroscopy  qubit $env.qubit  timeout 1200  note "fine spec"  args {{ freq range {f0} {f1} {step}  spec_amp {amp}  update false }} }}
  say "dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""


def qwf_pi(amp):
    return f"""workflow q8_piamp {{
  meta {{ name "q8_piamp"  description "q8ld3 π 脉冲幅度扫描"  tags [piamp] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 1800  max_writes 2  max_datasets 2  max_scan_points 60 }}
  goal {{ objective "找到 π 脉冲幅度"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.piamp  qubit $env.qubit  timeout 900  note "piamp"  args {{ amp {amp}  update false }} }}
  measure as c2 {{ fn sq.iqraw  qubit $env.qubit  timeout 600  note "iqraw"  args {{ update false }} }}
  say "pi=${{c1.dataset}} iq=${{c2.dataset}}"
}}
"""


def qwf_pi2(amp, df):
    return f"""workflow q8_pi_pidf {{
  meta {{ name "q8_pi_pidf"  description "q8ld3 piamp + pidf 联合检查"  tags [piamp, pidf] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 2400  max_writes 3  max_datasets 3  max_scan_points 120 }}
  goal {{ objective "检查 π 激发与 pidf 条纹"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.piamp  qubit $env.qubit  timeout 900  note "piamp"  args {{ amp {amp}  update false }} }}
  measure as c2 {{ fn sq.pidf  qubit $env.qubit  timeout 900  note "pidf df"  args {{ df {df}  update false }} }}
  measure as c3 {{ fn sq.iqraw  qubit $env.qubit  timeout 600  note "iqraw"  args {{ update false }} }}
  say "pi=${{c1.dataset}} pidf=${{c2.dataset}} iq=${{c3.dataset}}"
}}
"""


def go(text, tag):
    ir, err = parse_validate(text)
    if err:
        print("PARSE/VALIDATE FAIL:", json.dumps(err, ensure_ascii=False)[:800])
        return 1
    dry = run_ir(ir, dry=True, timeout=300)
    print("dryRun:", dry.get("status"), flush=True)
    st = run_ir(ir, dry=False)
    print("run:", st.get("status"), "outcome:", json.dumps(st.get("outcome"), ensure_ascii=False)[:200])
    print("nodes:", json.dumps(st.get("node_map"), ensure_ascii=False)[:400])
    json.dump(st, open(f"D:/qmclaw/_q8_{tag}_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0


def main():
    mode = sys.argv[1]
    if mode == "set":
        return cmd_set(sys.argv[2:])
    if mode == "reg":
        f = flat(reg())
        for k in ("f10", "f21", "fread", "bias_z", "ReadIn.power",
                  "PiGate.amp", "PiGate.length", "PiGate.alpha"):
            print("  %-14s %s" % (k, f.get(k)))
        return 0
    if mode == "spec":
        f0, f1, step, amp = sys.argv[2:6]
        return go(qwf_spec(f0, f1, step, amp), "spec_fine")
    if mode == "pi":
        return go(qwf_pi(sys.argv[2]), "piamp")
    if mode == "pi2":
        return go(qwf_pi2(sys.argv[2], sys.argv[3]), "pi_pidf")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
