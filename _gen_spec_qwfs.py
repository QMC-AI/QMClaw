# _gen_spec_qwfs.py — 生成本轮 q7ld3/q2ld3 扫频与 iqraw 用的 .qwf 模板
# 用法: python _gen_spec_qwfs.py
import os, io

OUT_DIR = r"D:\qmclaw"
SESSION = "20251103_LD_SQ"


def spec_qwf(qubit, tag, desc, freq_lo, freq_hi, step, amp, note):
    fname = f"{qubit}_spec_{tag}.qwf"
    path = os.path.join(OUT_DIR, fname)
    text = f"""# {fname} — {desc}
# freq {freq_lo}-{freq_hi} GHz step {step} GHz ({int(round((freq_hi-freq_lo)/step))} pts), spec_amp={amp}
workflow {qubit}_spec_{tag} {{
  meta {{
    name        "{qubit} spectroscopy {desc}"
    description "{desc}"
    tags        ["single-qubit", "spectroscopy", "{qubit}"]
  }}

  env {{
    qubit   "{qubit}"
    session "{SESSION}"
  }}

  budget {{
    max_wall   3600
    max_writes 2
    max_datasets 1
    max_scan_points 2000
  }}

  goal {{
    objective   "{desc}"
    success {{
      metric     $c1.dataset
      operator   >=
      value      0
    }}
    max_rounds  1
    on_achieved "continue"
  }}

  measure as c1 {{
    fn      sq.spectroscopy
    qubit   $env.qubit
    timeout 1200
    note "{note}"
    args {{
      freq range {freq_lo} {freq_hi} {step}
      spec_amp {amp}
      update false
    }}
  }}

  say "{qubit} spectroscopy dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("wrote", path)
    return path


def iqraw_qwf(qubit, tag, desc, note):
    fname = f"{qubit}_iqraw_{tag}.qwf"
    path = os.path.join(OUT_DIR, fname)
    text = f"""# {fname} — {desc}
workflow {qubit}_iqraw_{tag} {{
  meta {{
    name        "{qubit} iqraw {desc}"
    description "{desc}"
    tags        ["single-qubit", "iqraw", "{qubit}"]
  }}

  env {{
    qubit   "{qubit}"
    session "{SESSION}"
  }}

  budget {{
    max_wall   1200
    max_writes 2
    max_datasets 1
    max_scan_points 2000
  }}

  goal {{
    objective   "{desc}"
    success {{
      metric     $c1.dataset
      operator   >=
      value      0
    }}
    max_rounds  1
    on_achieved "continue"
  }}

  measure as c1 {{
    fn      sq.iqraw
    qubit   $env.qubit
    timeout 600
    note "{note}"
    args {{
      update false
    }}
  }}

  say "{qubit} iqraw dataset=${{c1.dataset}} status=${{c1.status}}"
}}
"""
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("wrote", path)
    return path


if __name__ == "__main__":
    # E0 对照：q2ld3 已知活比特（f10=3.6219），同一套代码路径/幅度
    spec_qwf("q2ld3", "ctrl_a050", "control run on q2ld3 (known live qubit f10=3.6219), spec_amp=0.05",
             3.0, 4.3, 0.0025, 0.05, "control: q2ld3, freq 3.0-4.3 GHz step 2.5 MHz, spec_amp=0.05, update=false")
    # E1: q7ld3 @ bias_z=-0.5，三档幅度
    for amp, tag in ((0.0232, "a023"), (0.05, "a050"), (0.1, "a100")):
        spec_qwf("q7ld3", f"bm05_{tag}", f"q7ld3 spectroscopy @ bias_z=-0.5, spec_amp={amp}",
                 3.0, 4.3, 0.0025, amp, f"bias_z=-0.5, freq 3.0-4.3 GHz step 2.5 MHz, spec_amp={amp}, update=false")
    # E2: q7ld3 @ bias_z=0
    for amp, tag in ((0.05, "a050"), (0.1, "a100")):
        spec_qwf("q7ld3", f"b000_{tag}", f"q7ld3 spectroscopy @ bias_z=0, spec_amp={amp}",
                 3.0, 4.3, 0.0025, amp, f"bias_z=0, freq 3.0-4.3 GHz step 2.5 MHz, spec_amp={amp}, update=false")
    # E3: q7ld3 @ bias_z=+1
    for amp, tag in ((0.05, "a050"), (0.1, "a100")):
        spec_qwf("q7ld3", f"bp01_{tag}", f"q7ld3 spectroscopy @ bias_z=+1, spec_amp={amp}",
                 3.0, 4.3, 0.0025, amp, f"bias_z=+1, freq 3.0-4.3 GHz step 2.5 MHz, spec_amp={amp}, update=false")
    # E4: iqraw 态区分（flux 点 -1 / -0.5 / 0 / +1）
    for tag, desc in (("bm1", "bias_z=-1"), ("bm05", "bias_z=-0.5"),
                       ("b000", "bias_z=0"), ("bp01", "bias_z=+1")):
        iqraw_qwf("q7ld3", tag, f"state separation (visibility/contrast/SNR) at {desc}",
                  f"{desc}, sq.iqraw auto, update=false")
    print("done")
