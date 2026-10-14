import labrad
from lqms.measure import generate_qubit
from lqms.pyle.registry_wrapper2 import RegistryWrapper
import traceback

print('Step 1: Connect to LabRAD...', flush=True)
cxn = labrad.connect()
reg = cxn.registry

print('Step 2: Navigate to session...', flush=True)
reg.cd(['', 'LQHL', 'test', '20260822_device1'])

print('Step 3: Create RegistryWrapper...', flush=True)
s = RegistryWrapper(reg)

print('Step 4: Generate qubits...', flush=True)
qubits = generate_qubit({'s': s}, info=None, sample=s)
print(f'Generated {len(qubits)} qubits', flush=True)

print('Step 5: Get q25_7...', flush=True)
qobj = qubits.get('q25_7')
if not qobj:
    print('ERROR: q25_7 not found!', flush=True)
    print('Available:', list(qubits.keys())[:20], flush=True)
    exit(1)

print(f'Got q25_7: {type(qobj).__name__}', flush=True)

print('Step 6: Check qubit attributes...', flush=True)
for attr in ['f10', 'piLen', 'piAmp', 'zpa', 'fread', 'qread', 'qrr']:
    try:
        val = getattr(qobj, attr, 'N/A')
        print(f'  {attr}: {val}', flush=True)
    except Exception as e:
        print(f'  {attr}: Error - {e}', flush=True)

print('Step 7: Run sq.iqraw...', flush=True)
from lqms.measure.tuners import sq_nodes as sq

try:
    result = sq.iqraw(qobj, do_plot=False)
    print(f'SUCCESS! Result: {result}', flush=True)
except Exception as e:
    print(f'ERROR: {type(e).__name__}: {e}', flush=True)
    traceback.print_exc()
