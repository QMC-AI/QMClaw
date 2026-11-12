import json, sys
ROOT = r"D:/qmclaw/Agentic Workflow/qmclaw-server/services/workflow_service"
sys.path.insert(0, ROOT); sys.dont_write_bytecode = True
from dsl import parse_qwf
from dsl.textdsl.printer import ir_to_qwf

GOAL_QWF = open("D:/qmclaw/_p1_goal_dsl_check.py", encoding="utf-8").read()
GOAL_QWF = GOAL_QWF.split('GOAL_QWF = """',1)[1].split('"""',1)[0]
ir = parse_qwf(GOAL_QWF)
text = ir_to_qwf(ir)
print("---- printed text ----")
print(text)
ir2 = parse_qwf(text)
print("---- pipeline diff ----")
print("P1:", json.dumps(ir["pipeline"], ensure_ascii=False, indent=1, sort_keys=True))
print("P2:", json.dumps(ir2["pipeline"], ensure_ascii=False, indent=1, sort_keys=True))
