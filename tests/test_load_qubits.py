import labrad
from lqms.measure import generate_qubit

# 连接 LabRAD
cxn = labrad.connect()
reg = cxn.registry

# 直接 cd 到目标目录
print('Navigating to LQHL/test/20260822_device1...')
reg.cd(['', 'LQHL', 'test', '20260822_device1'])
print('Navigation successful')

# 创建 wrapper
from lqms.pyle.registry_wrapper2 import RegistryWrapper
s = RegistryWrapper(reg)

# 加载 qubits
print('\nGenerating qubits...')
qubits = generate_qubit({'s': s}, info=None, sample=s)

print(f'Found {len(qubits)} qubits: {list(qubits.keys())[:10]}...')

# 选择第一个 qubit
if qubits:
    qobj = list(qubits.values())[0]
    print(f'\nFirst qubit type: {type(qobj).__name__}')

    # 查看关键属性
    print('\nKey qubit attributes:')
    for attr in ['f10', 'piLen', 'piAmp', 'zpa', 'fread', 'qread']:
        try:
            val = getattr(qobj, attr, 'N/A')
            print(f'  {attr}: {val}')
        except Exception as e:
            print(f'  {attr}: Error')

# 保存 qubit 以便后续使用
import json
with open('D:/QMClaw/test_qubit_info.json', 'w') as f:
    info = {
        'qubits': list(qubits.keys()),
        'qubit_type': str(type(qobj).__name__) if qubits else None
    }
    json.dump(info, f, indent=2)
print('\nQubit info saved to D:/QMClaw/test_qubit_info.json')
