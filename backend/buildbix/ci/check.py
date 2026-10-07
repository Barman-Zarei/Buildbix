"""Run after pasting your own workflow into ci/templates/:  python -m buildbix.ci.check"""
import sys

from . import registry

if __name__ == "__main__":
    errs = registry.check_templates()
    for t in registry.TEMPLATES:
        print(f"{t}: {registry.status(t)}")
    print("\n".join(errs) if errs else "All templates OK")
    sys.exit(1 if errs else 0)
