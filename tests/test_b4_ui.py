"""Package B4 (PRD 13.1): the operator page. Tier 1, static and render checks.

UAT-UI-01 to UI-11 are manual browser checks (UAT 11.1, tier 5) and stay
manual. These tests pin what can be checked offline: plain files and no build
step, the exact PRD 12 routes, the INV-11 labels, the regions of PRD 13.1, safe
rendering of untrusted text, and a render of a full /api/state snapshot in Node
with a minimal DOM stand-in (tests/ui_render_smoke.js).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8") if (STATIC / "index.html").exists() else ""
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8") if (STATIC / "app.js").exists() else ""
FIXTURES = json.loads((ROOT / "fixtures" / "manifest.json").read_text(encoding="utf-8"))["fixtures"]


def _prd_api_routes() -> set[tuple[str, str]]:
    text = (ROOT / "docs" / "PRD.md").read_text(encoding="utf-8")
    section = text[text.index("## 12. HTTP API"):text.index("### 12.1")]
    return {(m.group(1), m.group(2))
            for m in re.finditer(r"^\| `(GET|POST) (/api/\S+)`", section, flags=re.MULTILINE)}


def _js_routes() -> set[tuple[str, str]]:
    """Every entry of the API table in app.js, as (method, PRD-style path)."""
    block = re.search(r"const API = \{(.*?)\n\};", APP_JS, flags=re.DOTALL).group(1)
    routes = set()
    for method, path in re.findall(r'\[\s*"(GET|POST)",\s*[`"](/api/[^`"]*)[`"]', block):
        routes.add((method, re.sub(r"\$\{[^}]+\}", "{id}", path)))
    return routes


# ---- plain files, no build step (PRD 3.2, 4.1) ----------------------------

def test_b4_static_files_exist_and_load_only_local_assets():
    for name in ("index.html", "app.js", "style.css"):
        assert (STATIC / name).is_file(), name
    assert '<link rel="stylesheet" href="style.css">' in INDEX
    assert '<script src="app.js"></script>' in INDEX
    assert not re.search(r'(src|href)="(https?:)?//', INDEX), "no external scripts or styles"
    assert not re.search(r"\bimport\s|require\(", APP_JS.split("// ---- node export")[0])


def test_b4_no_build_pipeline_or_framework():
    for name in ("package.json", "package-lock.json", "vite.config.ts", "webpack.config.js",
                 "tsconfig.json", "node_modules"):
        assert not (ROOT / name).exists(), name
        assert not (STATIC / name).exists(), name
    assert not [p for p in STATIC.rglob("*") if p.suffix in (".ts", ".tsx", ".jsx", ".vue")]
    for framework in ("React", "Vue", "angular", "jQuery", "$("):
        assert framework not in APP_JS


def test_b4_page_is_served_at_root_without_login(client):
    page = client.get("/", follow_redirects=False)

    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'id="reset-a"' in page.text and 'id="reset-b"' in page.text
    assert "SANDBOX, no real money" in page.text
    assert 'type="password"' not in page.text and "sign in" not in page.text.lower()
    assert "/login" not in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


# ---- routes (PRD 12) ------------------------------------------------------

def test_b4_every_api_call_is_a_prd_route():
    assert _js_routes() <= _prd_api_routes()


def test_b4_the_page_uses_every_route_the_demo_needs():
    needed = {
        ("GET", "/api/state"), ("GET", "/api/health"), ("POST", "/api/demo/reset"),
        ("POST", "/api/milestones/{id}/deliverable"), ("POST", "/api/milestones/{id}/pay"),
        ("POST", "/api/payouts/{id}/retry"), ("POST", "/api/messages"),
        ("POST", "/api/cases/{id}/investigate"), ("POST", "/api/cases/{id}/actions/cancel"),
        ("POST", "/api/cases/{id}/actions/reissue"), ("POST", "/api/cases/{id}/actions/reconcile"),
        ("POST", "/api/cases/{id}/actions/recheck"),
    }
    assert needed <= _js_routes()


def test_b4_api_paths_appear_only_in_the_api_table():
    outside = APP_JS.replace(re.search(r"const API = \{.*?\n\};", APP_JS, flags=re.DOTALL).group(0), "")
    assert "/api/" not in outside


def test_b4_upload_is_multipart_with_the_prd_field_names():
    assert "new FormData()" in APP_JS
    for field in ('"csv_file"', '"report_file"', '"submitted_by"'):
        assert f"form.append({field}" in APP_JS


def test_b4_fixture_buttons_are_the_four_prd_fixtures():
    listed = re.search(r"const FIXTURES = (\{.*?\});", APP_JS, flags=re.DOTALL).group(1)
    fixtures = json.loads(re.sub(r"(\w+):", r'"\1":', listed))
    assert fixtures == {name: entry["milestone_id"] for name, entry in FIXTURES.items()}


def test_b4_pay_sends_the_displayed_amount_and_no_override():
    assert "expected_amount_cents: milestone.amount_cents" in APP_JS
    assert "override_reason" not in APP_JS  # PRD 14.1 cut 1: no override control in the UI


def test_b4_state_polls_every_two_seconds():
    assert re.search(r"const POLL_MS = 2000;", APP_JS)


# ---- scenario (decided by Gate 0, set by the human) -----------------------

def test_b4_scenario_b_is_the_primary_reset_and_a_stays_available():
    assert re.search(r'const PRIMARY_SCENARIO = "B";', APP_JS)
    b = re.search(r'<button[^>]*id="reset-b"[^>]*>', INDEX).group(0)
    a = re.search(r'<button[^>]*id="reset-a"[^>]*>', INDEX).group(0)
    assert 'class="primary"' in b and 'class="primary"' not in a
    assert INDEX.index('id="reset-b"') < INDEX.index('id="reset-a"')


# ---- labels and regions (PRD 13.1, INV-11) --------------------------------

def test_b4_page_has_every_region_of_prd_13_1():
    for region in ("contract", "milestones", "ledger", "inbox", "cases", "timeline"):
        assert f'id="{region}"' in INDEX, region


def test_b4_seeded_and_simulated_parts_are_labeled():
    assert re.search(r'id="contract".*?Seeded', INDEX, flags=re.DOTALL)
    assert re.search(r'id="inbox".*?Simulated', INDEX, flags=re.DOTALL)
    assert "New email confirmed by phone (simulated)" in APP_JS
    assert "Seeded fixture" in APP_JS
    for part in ("Real PayPal sandbox", "Seeded", "Simulated"):
        assert part in INDEX


def test_b4_two_key_strip_labels():
    for label in ("AI recommends", "Gate checks", "Human approves"):
        assert f'"{label}"' in APP_JS


def test_b4_every_prd_state_has_a_badge():
    for state in ("AWAITING_DELIVERABLE", "PASS", "FAIL", "NEEDS_REVIEW", "PAYOUT_IN_FLIGHT",
                  "PAID", "CREATING", "UNKNOWN", "SENT", "UNCLAIMED", "RETURNED", "FAILED",
                  "OPEN", "INVESTIGATED", "NEEDS_HUMAN", "AWAITING_REISSUE", "RESOLVED"):
        assert f'.state-{state}' in (STATIC / "style.css").read_text(encoding="utf-8"), state


def test_b4_untrusted_text_is_never_parsed_as_html():
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function"):
        assert sink not in APP_JS, sink


# ---- render smoke test in Node --------------------------------------------

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_b4_javascript_parses():
    result = subprocess.run([NODE, "--check", str(STATIC / "app.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_b4_renders_a_full_state_snapshot():
    result = subprocess.run([NODE, str(ROOT / "tests" / "ui_render_smoke.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["errors"] == []
    text = report["text"]
    for expected in ("PAY-4", "no_live_payout", "RECONCILE_MARK_PAID", "duplicate_email_count = 37",
                     "Injection attempt flagged", "MISTYPED_RECIPIENT", "Diversion risk",
                     "FAKEB0001", "PAYPAL_PAYOUT_CREATED", "Approve and pay $300.00",
                     "<script>alert(1)</script>"):
        assert expected in text, expected
    assert report["script_elements"] == 0  # the hostile message stayed text
    assert report["pay_disabled"] == {"m1": False, "m2": True}
