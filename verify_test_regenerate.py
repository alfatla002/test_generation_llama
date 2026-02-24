# verify_tests.py
# Enhanced with iterative test regeneration using Claude API
# If a test doesn't achieve fail-to-pass, it captures the error context
# and asks Claude to fix/regenerate the test, up to MAX_RETRIES times.

import subprocess
import json
import shutil
import re
import os
from pathlib import Path
from datetime import datetime

import anthropic

# ─── Configuration ───────────────────────────────────────────────
MAX_RETRIES = 3                    # max regeneration attempts per test
MODEL = "claude-sonnet-4-20250514"  # claude model for regeneration
MAX_TOKENS = 4096
TIMEOUT_SECONDS = 120
DEP_INSTALL_TIMEOUT = 180
# ─────────────────────────────────────────────────────────────────


def run_command(cmd, cwd=None, timeout=TIMEOUT_SECONDS):
    """Run a shell command and return result."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-3000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s: {cmd}",
            "timed_out": True,
        }


def detect_test_command(repo_dir, test_path):
    """Auto-detect how to run tests based on project structure."""
    repo_dir = Path(repo_dir)

    if test_path.endswith(".py"):
        return f"python -m pytest {test_path} -v --tb=long"

    if test_path.endswith((".js", ".ts", ".jsx", ".tsx")):
        if (repo_dir / "jest.config.js").exists() or (repo_dir / "jest.config.ts").exists():
            return f"npx jest {test_path} --no-coverage --verbose"
        if (repo_dir / "vitest.config.ts").exists():
            return f"npx vitest run {test_path}"
        return f"npx jest {test_path} --no-coverage --verbose"

    if test_path.endswith(".go"):
        return f"go test -v -run . ./{Path(test_path).parent}"

    return f"python -m pytest {test_path} -v --tb=long"


def install_dependencies(clone_dir):
    """Install project dependencies based on detected project type."""
    clone_dir = Path(clone_dir)
    if (clone_dir / "package.json").exists():
        run_command("npm install --silent 2>/dev/null", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)
    elif (clone_dir / "requirements.txt").exists():
        run_command("pip install -r requirements.txt -q", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)
    elif (clone_dir / "pyproject.toml").exists():
        run_command("pip install -e . -q", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)
    elif (clone_dir / "setup.py").exists():
        run_command("pip install -e . -q", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)


def run_test_at_commit(clone_dir, commit_sha, test_source, suggested_path, label=""):
    """
    Checkout a specific commit, place the test file, install deps, and run.
    Returns (passed: bool, run_result: dict)
    """
    clone_dir = Path(clone_dir)
    test_dest = clone_dir / suggested_path

    # Checkout commit (clean state)
    run_command("git checkout -- .", cwd=clone_dir)
    run_command("git clean -fd", cwd=clone_dir)
    res = run_command(f"git checkout {commit_sha}", cwd=clone_dir)
    if res["returncode"] != 0:
        return None, {"error": f"Checkout failed: {res['stderr']}"}

    # Place test file
    test_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(test_source, test_dest)

    # Install dependencies
    install_dependencies(clone_dir)

    # Run test
    test_cmd = detect_test_command(clone_dir, suggested_path)
    print(f"  Running: {test_cmd}")
    result = run_command(test_cmd, cwd=clone_dir, timeout=TIMEOUT_SECONDS)
    passed = result["returncode"] == 0

    status_icon = "✓ PASS" if passed else "✗ FAIL"
    print(f"  {label}: {status_icon}")

    return passed, result


# ─── Diagnosis Helpers ───────────────────────────────────────────

def diagnose_failure(status, pre_result, post_result, test_code):
    """
    Analyze WHY the test didn't achieve fail-to-pass.
    Returns a structured diagnosis to send to Claude for retry.
    """
    diagnosis = {
        "status": status,
        "problem": "",
        "pre_fix_output": "",
        "post_fix_output": "",
        "error_locations": [],
    }

    if status == "PASS_TO_PASS":
        diagnosis["problem"] = (
            "The test PASSED on BOTH the buggy and fixed code. "
            "This means the test does NOT exercise the actual buggy behavior. "
            "You need to write a test that specifically triggers the bug — "
            "it must FAIL when the bug is present."
        )
        diagnosis["pre_fix_output"] = _format_output(pre_result)

    elif status == "FAIL_TO_FAIL":
        diagnosis["problem"] = (
            "The test FAILED on BOTH the buggy and fixed code. "
            "This likely means the test itself has errors (syntax errors, "
            "import errors, wrong function names, incorrect assertions) "
            "or tests something unrelated to the bug fix."
        )
        diagnosis["pre_fix_output"] = _format_output(pre_result)
        diagnosis["post_fix_output"] = _format_output(post_result)

        # Extract specific error lines
        diagnosis["error_locations"] = _extract_error_locations(
            pre_result, post_result, test_code
        )

    elif status == "PASS_TO_FAIL":
        diagnosis["problem"] = (
            "The test PASSED on buggy code but FAILED on fixed code. "
            "This is backwards — the test is asserting the buggy behavior "
            "as correct. Flip the assertions so the test expects the FIXED "
            "behavior as correct."
        )
        diagnosis["post_fix_output"] = _format_output(post_result)

    return diagnosis


def _format_output(result):
    """Format test run output for Claude, combining stdout + stderr."""
    if result is None:
        return "No output available"
    parts = []
    if result.get("stderr"):
        parts.append(f"STDERR:\n{result['stderr']}")
    if result.get("stdout"):
        parts.append(f"STDOUT:\n{result['stdout']}")
    return "\n\n".join(parts) if parts else "No output captured"


def _extract_error_locations(pre_result, post_result, test_code):
    """
    Parse error tracebacks to find specific lines and error types.
    Returns a list of {line, error_type, message} dicts.
    """
    errors = []
    combined_output = ""
    for r in [pre_result, post_result]:
        if r:
            combined_output += r.get("stdout", "") + "\n" + r.get("stderr", "")

    # Python traceback patterns
    # Match: File "test_xxx.py", line 42, in test_something
    py_pattern = r'File ".*?", line (\d+).*?\n\s*(.+)\n(\w+Error.*?)$'
    for match in re.finditer(py_pattern, combined_output, re.MULTILINE):
        errors.append({
            "line": int(match.group(1)),
            "code": match.group(2).strip(),
            "error_type": match.group(3).strip(),
        })

    # AssertionError patterns
    assert_pattern = r'(AssertionError|assert\s+.+)'
    for match in re.finditer(assert_pattern, combined_output):
        errors.append({
            "error_type": "AssertionError",
            "message": match.group(0).strip()[:200],
        })

    # ImportError / ModuleNotFoundError
    import_pattern = r'(ImportError|ModuleNotFoundError):\s*(.+)'
    for match in re.finditer(import_pattern, combined_output):
        errors.append({
            "error_type": match.group(1),
            "message": match.group(2).strip(),
        })

    # JavaScript/Jest error patterns
    js_pattern = r'●\s+(.*?)\n\n\s*(.*?)(?:\n\n|\Z)'
    for match in re.finditer(js_pattern, combined_output, re.DOTALL):
        errors.append({
            "error_type": "JestFailure",
            "test_name": match.group(1).strip(),
            "message": match.group(2).strip()[:300],
        })

    return errors


# ─── Regeneration via Claude API ─────────────────────────────────

def regenerate_test(bug_data, previous_test_code, diagnosis, attempt):
    """
    Send the failed test + diagnosis back to Claude for a refined attempt.
    """
    client = anthropic.Anthropic()

    # Build context from bug data
    files_section = ""
    for f in bug_data.get("files", []):
        files_section += f"""
### File: `{f['filename']}`
**Diff:**
```diff
{f.get('patch', 'N/A')[:2000]}
```
**Buggy version (before fix):**
```
{(f.get('before_fix') or 'N/A')[:2000]}
```
**Fixed version (after fix):**
```
{(f.get('after_fix') or 'N/A')[:2000]}
```
"""

    error_details = ""
    if diagnosis["error_locations"]:
        error_details = "\n## Specific Errors Found\n"
        for err in diagnosis["error_locations"]:
            error_details += f"- **{err.get('error_type', 'Error')}**"
            if err.get("line"):
                error_details += f" at line {err['line']}"
            if err.get("message"):
                error_details += f": {err['message']}"
            if err.get("code"):
                error_details += f"\n  Code: `{err['code']}`"
            error_details += "\n"

    prompt = f"""You are a senior test engineer. This is ATTEMPT {attempt + 1} of {MAX_RETRIES + 1}.

## Bug Report (Issue #{bug_data['issue_number']})
**Title:** {bug_data['issue_title']}
**Description:**
{bug_data.get('issue_body', 'N/A')[:1500]}

## Fix (PR #{bug_data['pr_number']})
**Title:** {bug_data['pr_title']}
**Description:**
{bug_data.get('pr_body', 'N/A')[:1500]}

## Changed Files
{files_section}

---

## ❌ PREVIOUS TEST FAILED VERIFICATION

**Status:** {diagnosis['status']}
**Problem:** {diagnosis['problem']}

### Previous Test Code (that didn't work):
```
{previous_test_code}
```

### Test Output on BUGGY code (pre-fix):
```
{diagnosis.get('pre_fix_output', 'N/A')[:2000]}
```

### Test Output on FIXED code (post-fix):
```
{diagnosis.get('post_fix_output', 'N/A')[:2000]}
```
{error_details}

---

## Your Task
Fix the test so it achieves **FAIL-TO-PASS**:
- It MUST **FAIL** on the buggy (pre-fix) code
- It MUST **PASS** on the fixed (post-fix) code

### Key fixes needed based on the diagnosis:
{"- The test doesn't trigger the bug. Rewrite assertions to test the EXACT buggy behavior." if diagnosis['status'] == 'PASS_TO_PASS' else ""}
{"- The test has errors on both versions. Fix syntax/import/assertion errors first, then ensure it targets the bug." if diagnosis['status'] == 'FAIL_TO_FAIL' else ""}
{"- The assertions are backwards. The test should expect CORRECT behavior (which fails on buggy code)." if diagnosis['status'] == 'PASS_TO_FAIL' else ""}

### Rules:
1. Analyze the error output carefully — fix the SPECIFIC errors shown.
2. Make sure imports are correct for the project.
3. Use the EXACT function/class/method names from the source code.
4. The test should assert the CORRECT (fixed) behavior, so it fails when the bug is present.
5. Return ONLY the complete test file content in a single code block.
6. Include a comment at the top with the suggested file path.
"""

    print(f"  🤖 Calling Claude API (attempt {attempt + 1})...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        system=(
            "You are an expert test engineer specializing in regression tests. "
            "You are fixing a test that failed verification. Be precise about "
            "imports, function names, and assertions. Analyze the error output carefully."
        ),
    )

    text = response.content[0].text
    code_match = re.search(r'```[\w]*\n(.*?)```', text, re.DOTALL)
    return code_match.group(1) if code_match else text


# ─── Main Verification with Retry Loop ───────────────────────────

def verify_single_test(repo_url, test_info, bug_data=None, workspace="workspace"):
    """
    Verify a test with iterative retry:
    1. Run fail-to-pass check
    2. If it fails, diagnose the problem
    3. Regenerate the test via Claude
    4. Repeat up to MAX_RETRIES times
    """
    issue = test_info["issue_number"]
    clone_dir = Path(workspace) / f"repo_issue_{issue}"
    test_source = Path(test_info["test_file"])
    suggested_path = test_info["suggested_path"]
    pre_fix_sha = test_info["pre_fix_sha"]
    fix_sha = test_info["merge_commit_sha"]

    print(f"\n{'═'*60}")
    print(f"  Verifying Issue #{issue}")
    print(f"  Pre-fix SHA:  {pre_fix_sha[:10]}")
    print(f"  Fix SHA:      {fix_sha[:10]}")
    print(f"{'═'*60}")

    # ── Clone repo (once) ──
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    print("📥 Cloning repository...")
    res = run_command(f"git clone --quiet {repo_url} {clone_dir}")
    if res["returncode"] != 0:
        return {"issue": issue, "status": "CLONE_FAILED", "error": res["stderr"]}

    # ── Iterative verification loop ──
    current_test_source = test_source
    attempt_history = []

    for attempt in range(MAX_RETRIES + 1):  # 0 = initial, 1..N = retries
        is_retry = attempt > 0
        label = f"Attempt {attempt + 1}/{MAX_RETRIES + 1}"
        print(f"\n{'─'*40}")
        print(f"  🔄 {label}{'  (retry)' if is_retry else '  (initial)'}")
        print(f"{'─'*40}")

        # Run on BUGGY code
        print(f"  🧪 Running on BUGGY code...")
        pre_passed, pre_result = run_test_at_commit(
            clone_dir, pre_fix_sha, current_test_source, suggested_path,
            label="Pre-fix (buggy)"
        )

        if pre_passed is None:
            return {"issue": issue, "status": "CHECKOUT_FAILED", "error": str(pre_result)}

        # Run on FIXED code
        print(f"  🧪 Running on FIXED code...")
        post_passed, post_result = run_test_at_commit(
            clone_dir, fix_sha, current_test_source, suggested_path,
            label="Post-fix (fixed)"
        )

        if post_passed is None:
            return {"issue": issue, "status": "CHECKOUT_FAILED", "error": str(post_result)}

        # ── Determine result ──
        if not pre_passed and post_passed:
            status = "FAIL_TO_PASS"
        elif pre_passed and post_passed:
            status = "PASS_TO_PASS"
        elif not pre_passed and not post_passed:
            status = "FAIL_TO_FAIL"
        else:
            status = "PASS_TO_FAIL"

        current_test_code = current_test_source.read_text()

        attempt_record = {
            "attempt": attempt + 1,
            "status": status,
            "pre_fix_passed": pre_passed,
            "post_fix_passed": post_passed,
            "pre_fix_stdout": pre_result.get("stdout", "")[-500:],
            "pre_fix_stderr": pre_result.get("stderr", "")[-500:],
            "post_fix_stdout": post_result.get("stdout", "")[-500:],
            "post_fix_stderr": post_result.get("stderr", "")[-500:],
        }
        attempt_history.append(attempt_record)

        # ── SUCCESS ──
        if status == "FAIL_TO_PASS":
            print(f"\n  ✅ FAIL_TO_PASS achieved on attempt {attempt + 1}!")
            # Save the successful test back
            if is_retry:
                shutil.copy(current_test_source, test_source)
                print(f"  💾 Updated test file: {test_source}")
            return {
                "issue": issue,
                "status": f"FAIL_TO_PASS ✅ (attempt {attempt + 1})",
                "attempts": attempt + 1,
                "attempt_history": attempt_history,
                "pre_fix_failed": True,
                "post_fix_passed": True,
                "pre_fix_output": pre_result,
                "post_fix_output": post_result,
            }

        # ── FAILED — can we retry? ──
        if attempt >= MAX_RETRIES:
            print(f"\n  ❌ Max retries ({MAX_RETRIES}) exhausted. Final status: {status}")
            break

        if bug_data is None:
            print(f"\n  ⚠️  No bug_data provided — cannot regenerate. Status: {status}")
            break

        # ── Diagnose & Regenerate ──
        print(f"\n  ⚠️  Status: {status} — diagnosing and regenerating...")
        diagnosis = diagnose_failure(status, pre_result, post_result, current_test_code)

        try:
            new_test_code = regenerate_test(bug_data, current_test_code, diagnosis, attempt)

            # Save regenerated test to a new file
            regen_path = test_source.parent / f"test_issue_{issue}_attempt{attempt + 1}.py"
            regen_path.write_text(new_test_code)
            current_test_source = regen_path
            print(f"  📝 Regenerated test saved: {regen_path.name}")

        except Exception as e:
            print(f"  ❌ Regeneration failed: {e}")
            break

    # ── All retries exhausted ──
    final_status = attempt_history[-1]["status"] if attempt_history else "UNKNOWN"
    return {
        "issue": issue,
        "status": f"{final_status} ❌ (exhausted {MAX_RETRIES + 1} attempts)",
        "attempts": len(attempt_history),
        "attempt_history": attempt_history,
        "pre_fix_failed": not (attempt_history[-1]["pre_fix_passed"] if attempt_history else True),
        "post_fix_passed": attempt_history[-1]["post_fix_passed"] if attempt_history else False,
    }


def verify_all(repo_url, manifest_path="generated_tests/manifest.json",
               bugs_file="collected_bugs.json"):
    """Verify all generated tests with iterative retry."""

    manifest = json.loads(Path(manifest_path).read_text())

    # Load bug data for regeneration context
    bugs_lookup = {}
    bugs_path = Path(bugs_file)
    if bugs_path.exists():
        bugs = json.loads(bugs_path.read_text())
        bugs_lookup = {b["issue_number"]: b for b in bugs}
        print(f"📂 Loaded bug data for {len(bugs_lookup)} issues")
    else:
        print(f"⚠️  No {bugs_file} found — retry/regeneration will be disabled")

    results = []
    for test_info in manifest:
        issue_num = test_info["issue_number"]
        bug_data = bugs_lookup.get(issue_num)

        try:
            result = verify_single_test(repo_url, test_info, bug_data=bug_data)
            results.append(result)
        except Exception as e:
            print(f"  ❌ Unexpected error for issue #{issue_num}: {e}")
            results.append({
                "issue": issue_num,
                "status": f"ERROR: {e}",
                "attempts": 0,
            })

    # ── Summary Report ──
    print(f"\n{'═'*60}")
    print("  VERIFICATION SUMMARY")
    print(f"{'═'*60}")
    total = len(results)
    f2p = sum(1 for r in results if "FAIL_TO_PASS" in str(r.get("status", "")))
    first_try = sum(
        1 for r in results
        if "FAIL_TO_PASS" in str(r.get("status", "")) and r.get("attempts") == 1
    )
    retried = sum(
        1 for r in results
        if "FAIL_TO_PASS" in str(r.get("status", "")) and r.get("attempts", 0) > 1
    )
    failed = total - f2p

    print(f"  Total tests:        {total}")
    print(f"  ✅ Fail-to-Pass:    {f2p} ({f2p/total*100:.0f}%)" if total else "")
    print(f"     ├─ First try:    {first_try}")
    print(f"     └─ After retry:  {retried}")
    print(f"  ❌ Not achieved:    {failed}")

    print(f"\n  Details:")
    for r in results:
        attempts_str = f" [{r.get('attempts', '?')} attempt(s)]"
        print(f"    Issue #{r['issue']}: {r['status']}{attempts_str}")

    # ── Save results ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(f"verification_results_{timestamp}.json")
    output_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  📄 Results saved: {output_path}")

    # Also save latest
    Path("verification_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python verify_tests.py <repo_url> [manifest] [bugs_file]")
        print("  repo_url:  https://github.com/owner/repo.git")
        print("  manifest:  generated_tests/manifest.json (default)")
        print("  bugs_file: collected_bugs.json (default)")
        sys.exit(1)

    repo_url = sys.argv[1]
    manifest = sys.argv[2] if len(sys.argv) > 2 else "generated_tests/manifest.json"
    bugs_file = sys.argv[3] if len(sys.argv) > 3 else "collected_bugs.json"

    verify_all(repo_url, manifest_path=manifest, bugs_file=bugs_file)