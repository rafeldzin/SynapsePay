"""UAT-ARCH-03, 04, 05: import rules of PRD 4.3, checked by parsing every module under app/ (INV-2, INV-3)."""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
LLM_SDK_ROOTS = {"google", "anthropic", "openai"}


def imports_of(path: Path) -> set[str]:
    """Every dotted module a file imports. `from app.paypal import writes` counts as app.paypal.writes."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def modules():
    return {p: imports_of(p) for p in APP.rglob("*.py")}


def rel(path: Path) -> str:
    return path.relative_to(APP.parent).as_posix()


def test_uat_arch_03_paypal_writes_is_imported_only_by_the_executor():
    importers = [rel(p) for p, found in modules().items() if "app.paypal.writes" in found]
    assert importers == ["app/gate/executor.py"]


def test_uat_arch_04_verify_investigate_and_llm_never_reach_a_write_path():
    forbidden = {"app.paypal.writes", "app.paypal.http", "app.gate.executor"}
    checked = [p for p in modules() if rel(p).startswith(("app/verify/", "app/investigate/")) or rel(p) == "app/llm.py"]
    assert checked, "expected modules to check"
    assert {rel(p): sorted(modules()[p] & forbidden) for p in checked if modules()[p] & forbidden} == {}


def test_uat_arch_05_the_llm_sdk_is_imported_only_by_llm_py():
    importers = sorted(rel(p) for p, found in modules().items()
                       if any(name.split(".")[0] in LLM_SDK_ROOTS for name in found))
    assert importers == ["app/llm.py"]


def test_a_arch_paypal_reads_is_imported_only_by_investigate_tools_policy_and_sync():
    # PRD 8.5 makes paypal/sync.py read the batch, so sync.py joins the two modules named in AGENTS.md
    # (agreed with Dev B). Reads are read-only, so INV-2 and INV-3 are unaffected.
    importers = sorted(rel(p) for p, found in modules().items() if "app.paypal.reads" in found)
    assert importers == ["app/gate/policy.py", "app/investigate/tools.py", "app/paypal/sync.py"]


def test_a_arch_the_policy_gate_and_paypal_package_never_import_llm_or_investigators():
    banned = {"app.llm", "app.verify", "app.investigate"}
    for p, found in modules().items():
        if rel(p).startswith(("app/gate/policy", "app/paypal/")):
            assert not any(name == b or name.startswith(b + ".") for name in found for b in banned), rel(p)
