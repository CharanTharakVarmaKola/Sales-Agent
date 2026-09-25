"""Root shim so `python3 compliance_gate.py` runs the real gate's self-test."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "orchestrator", "policy"))

if __name__ == "__main__":
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "real_compliance_gate",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "orchestrator", "policy", "compliance_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.exit(mod._selftest())
