"""Runtime check: which modules imported by the QMClaw project can actually be
resolved by the interpreter running this script?

Unlike a zip-metadata heuristic, this asks the real import system
(importlib.util.find_spec) for every top-level module the project imports.

Run with the target env's python:
    <env>\\python.exe -B check_runtime_imports.py [project_root] [extra_path ...]

Any extra_path arguments are prepended to sys.path, so you can test e.g.
    ... check_runtime_imports.py D:\\semester4\\QMClaw D:\\semester4\\QMClaw\\vendor\\hermes-agent
"""
import ast
import importlib.util
import os
import sys

DEFAULT_ROOT = r"D:\semester4\QMClaw"
# vendor/ 是第三方子模块代码（hermes-agent 有 6000+ 个 py 文件、大量可选平台适配器），
# 扫描它会淹没项目自身的依赖问题。需要连 vendor 一起扫时，把 "vendor" 从集合里去掉。
SKIP_DIRS = {".git", "node_modules", "__pycache__", "deploy_diff_tools",
             "_metadata_cache", ".next", "dist", "build", "vendor"}


def collect(root):
    imports = {}
    n_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                tree = ast.parse(open(fp, encoding="utf-8", errors="replace").read(), fp)
            except SyntaxError:
                continue
            n_files += 1
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        mods.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        mods.add(node.module.split(".")[0])
            for m in mods:
                imports.setdefault(m, set()).add(os.path.relpath(fp, root))
    return imports, n_files


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    extra = sys.argv[2:]
    for p in reversed(extra):
        sys.path.insert(0, p)

    os.chdir(root)
    sys.path.insert(0, root)

    imports, n_files = collect(root)
    stdlib = set(sys.stdlib_module_names)

    resolves, missing = [], []
    for m in sorted(imports):
        if m in stdlib or m.startswith("_"):
            continue
        try:
            spec = importlib.util.find_spec(m)
        except (ImportError, ValueError):
            spec = None
        (resolves if spec is not None else missing).append(m)

    print(f"project root : {root}")
    print(f"interpreter  : {sys.executable}")
    print(f"extra path   : {extra or '(none)'}")
    print(f"scanned      : {n_files} .py files, {len(imports)} distinct top-level imports")
    print("=" * 74)
    print(f"\n[可解析] {len(resolves)}")
    print("  " + ", ".join(resolves))
    print(f"\n[不可解析] {len(missing)}")
    for m in missing:
        files = sorted(imports[m])
        print(f"  {m}")
        for f in files[:4]:
            print(f"      {f}")
        if len(files) > 4:
            print(f"      ... +{len(files) - 4} more")


if __name__ == "__main__":
    main()
