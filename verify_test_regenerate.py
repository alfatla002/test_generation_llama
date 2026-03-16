# verify_tests.py
# Enhanced with iterative test regeneration using Ollama API
# If a test doesn't achieve fail-to-pass, it captures the error context
# and asks the model to fix/regenerate the test, up to MAX_RETRIES times.

import subprocess
import json
import shutil
import re
import os
import ast
from pathlib import Path
from datetime import datetime

import requests

# ─── Configuration ───────────────────────────────────────────────
MAX_RETRIES = 3                    # max regeneration attempts per test
MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "4096"))
TIMEOUT_SECONDS = 120
DEP_INSTALL_TIMEOUT = 180
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "1200"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
# ─────────────────────────────────────────────────────────────────


def _ollama_base_urls():
    urls = []
    primary = OLLAMA_BASE_URL.rstrip("/")
    urls.append(primary)
    if "127.0.0.1:11434" in primary or "localhost:11434" in primary:
        # WSL fallback: Windows host is usually the resolver nameserver.
        try:
            resolv = Path("/etc/resolv.conf").read_text()
            for line in resolv.splitlines():
                if line.startswith("nameserver "):
                    ip = line.split()[1].strip()
                    if ip:
                        urls.append(f"http://{ip}:11434")
                    break
        except Exception:
            pass
    return list(dict.fromkeys(urls))


def _ollama_chat(messages, system_prompt):
    payload = {
        "model": MODEL,
        "stream": False,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": MAX_TOKENS,
        },
    }
    last_error = None
    for base in _ollama_base_urls():
        try:
            response = requests.post(
                f"{base}/api/chat",
                json=payload,
                timeout=OLLAMA_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            last_error = f"{base}: {e}"
    raise RuntimeError(f"Ollama request failed for all endpoints. Last error: {last_error}")


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
        return

    if (clone_dir / "requirements.txt").exists():
        run_command("python -m pip install -r requirements.txt -q", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)
    if (clone_dir / "pyproject.toml").exists() or (clone_dir / "setup.py").exists():
        run_command("python -m pip install -e . -q", cwd=clone_dir, timeout=DEP_INSTALL_TIMEOUT)


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
    if suggested_path.endswith(".py"):
        collect_cmd = f"python -m pytest {suggested_path} --collect-only -q"
        collect_result = run_command(collect_cmd, cwd=clone_dir, timeout=TIMEOUT_SECONDS)
        if collect_result["returncode"] != 0:
            collect_result["preflight_collect_failed"] = True
            collect_result["test_cmd"] = collect_cmd
            print(f"  {label}: ✗ FAIL (collect-only)")
            return False, collect_result
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
        "missing_modules": _extract_missing_modules(pre_result, post_result),
    }

    if status == "PASS_TO_PASS":
        diagnosis["problem"] = (
            "The test PASSED on BOTH the buggy and fixed code. "
            "This means the test does NOT exercise the actual buggy behavior. "
            "You need to write a test that specifically triggers the bug — "
            "it must FAIL when the bug is present."
        )
        diagnosis["pre_fix_output"] = _format_output(pre_result)

    elif status in ("FAIL_TO_FAIL", "FAIL_TO_FAIL_ENV", "FAIL_TO_FAIL_IMPORT"):
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
        if diagnosis["missing_modules"]:
            diagnosis["problem"] += (
                " Optional dependencies are missing in the environment; "
                "test should avoid hard dependency on optional modules."
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


def _extract_missing_modules(*results):
    missing = set()
    for result in results:
        if not result:
            continue
        blob = (result.get("stdout", "") or "") + "\n" + (result.get("stderr", "") or "")
        for m in re.finditer(r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]", blob):
            missing.add(m.group(1))
    return sorted(missing)


def _normalize_python_test_code(code_text):
    """Apply lightweight fixes for common import omissions in generated tests."""
    if not code_text:
        return code_text
    out = code_text
    needs_pytest = "pytest." in out and not re.search(
        r"^\s*(?:import\s+pytest\b|from\s+pytest\b)",
        out,
        re.MULTILINE,
    )
    if needs_pytest:
        lines = out.splitlines()
        insert_at = 0
        if lines and lines[0].startswith("#!"):
            insert_at = 1
        while insert_at < len(lines) and (
            lines[insert_at].strip().startswith("#") or not lines[insert_at].strip()
        ):
            insert_at += 1
        lines.insert(insert_at, "import pytest")
        out = "\n".join(lines).rstrip() + "\n"
    return out


def _expand_missing_module_prefixes(missing_modules):
    expanded = set()
    for mod in missing_modules:
        parts = mod.split(".")
        for i in range(1, len(parts) + 1):
            expanded.add(".".join(parts[:i]))
    return sorted(expanded)


def _discover_repo_top_level_modules(clone_dir):
    """Best-effort discovery of repo-owned top-level Python package names."""
    clone_dir = Path(clone_dir)
    tops = set()
    src_dir = clone_dir / "src"
    if src_dir.exists():
        for p in src_dir.iterdir():
            if p.is_dir() and re.match(r"^[A-Za-z_]\w*$", p.name):
                tops.add(p.name)
    for p in clone_dir.iterdir():
        if p.is_dir() and (p / "__init__.py").exists() and re.match(r"^[A-Za-z_]\w*$", p.name):
            tops.add(p.name)
    return tops


def _filter_shimmable_modules(missing_modules, clone_dir):
    """
    Only shim likely external optional deps, never repo-owned package modules.
    """
    repo_tops = _discover_repo_top_level_modules(clone_dir)
    shimmable = []
    for mod in missing_modules:
        top = mod.split(".")[0]
        if top in repo_tops:
            continue
        shimmable.append(mod)
    return sorted(set(shimmable))


def _inject_missing_module_shims(code_text, missing_modules):
    """
    Insert a lightweight optional-dependency shim preamble for missing modules.
    This avoids collection-time import crashes on optional integrations.
    """
    if not code_text or not missing_modules:
        return code_text
    marker = "# AUTO-OPTIONAL-DEP-SHIMS"
    if marker in code_text:
        return code_text

    shim_block = (
        f"{marker}\n"
        "import sys\n"
        "import types\n\n"
        f"_MISSING_OPTIONAL_MODULES = {json.dumps(_expand_missing_module_prefixes(sorted(set(missing_modules))))}\n\n"
        "def _install_module_chain(mod_name):\n"
        "    parts = mod_name.split('.')\n"
        "    for i in range(1, len(parts) + 1):\n"
        "        name = '.'.join(parts[:i])\n"
        "        if name not in sys.modules:\n"
        "            module = types.ModuleType(name)\n"
        "            module.__path__ = []\n"
        "            module.__package__ = name.rpartition('.')[0]\n"
        "            def _fallback_attr(attr_name, _name=name):\n"
        "                if attr_name == '__path__':\n"
        "                    return []\n"
        "                return type(attr_name, (), {})\n"
        "            module.__getattr__ = _fallback_attr\n"
        "            sys.modules[name] = module\n"
        "    for i in range(1, len(parts)):\n"
        "        parent = '.'.join(parts[:i])\n"
        "        child = parts[i]\n"
        "        full = '.'.join(parts[:i + 1])\n"
        "        setattr(sys.modules[parent], child, sys.modules[full])\n\n"
        "for _mod in _MISSING_OPTIONAL_MODULES:\n"
        "    _install_module_chain(_mod)\n\n"
    )

    if code_text.startswith("#!"):
        lines = code_text.splitlines(True)
        return lines[0] + shim_block + "".join(lines[1:])
    return shim_block + code_text


def _sanitize_test_code(text):
    """Extract clean code and strip markdown fences/prose artifacts."""
    if not text:
        return ""
    blocks = re.findall(r"```(?:[\w.+-]+)?\n(.*?)```", text, re.DOTALL)
    candidate = blocks[0] if blocks else text
    lines = [ln for ln in candidate.splitlines() if not ln.strip().startswith("```")]

    # Drop leading prose and keep from first code-like line.
    code_start = 0
    code_markers = ("#", "import ", "from ", "def ", "class ", "@", "async ", "if __name__", '"""', "'''")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(code_markers):
            code_start = i
            break
    cleaned_lines = lines[code_start:]
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned + "\n" if cleaned else ""


def _has_repeated_bad_pattern(candidate_code, diagnosis):
    """Reject regenerated outputs that repeat known failing assertion patterns."""
    outputs = (diagnosis.get("pre_fix_output", "") or "") + "\n" + (diagnosis.get("post_fix_output", "") or "")
    if "ImportError not raised" in outputs:
        if "assertRaises(ImportError)" in candidate_code or "pytest.raises(ImportError)" in candidate_code:
            return True, "repeated ImportError assertion pattern"
    return False, ""


def _is_no_progress(attempt_history):
    """Detect repeated attempts with unchanged outcomes/output tails."""
    if len(attempt_history) < 2:
        return False
    prev = attempt_history[-2]
    cur = attempt_history[-1]
    return (
        prev.get("status") == cur.get("status")
        and prev.get("pre_fix_stdout") == cur.get("pre_fix_stdout")
        and prev.get("post_fix_stdout") == cur.get("post_fix_stdout")
    )


def _should_force_strict_mode(attempt_history):
    """
    Switch to strict rewrite mode after two failed attempts with no progress.
    """
    if len(attempt_history) < 2:
        return False
    last = attempt_history[-1]
    prev = attempt_history[-2]
    failed_statuses = {"FAIL_TO_FAIL", "FAIL_TO_FAIL_IMPORT", "PASS_TO_PASS", "PASS_TO_FAIL"}
    if last.get("status") not in failed_statuses or prev.get("status") not in failed_statuses:
        return False
    return _is_no_progress(attempt_history)


def _is_valid_python(code_text):
    if not code_text.strip():
        return False, "empty code"
    try:
        ast.parse(code_text)
        return True, ""
    except SyntaxError as e:
        return False, f"line {e.lineno}: {e.msg}"


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


# ─── Regeneration via Ollama API ─────────────────────────────────

def regenerate_test(bug_data, previous_test_code, diagnosis, attempt, suggested_path=None, force_rewrite=False):
    """
    Send the failed test + diagnosis back to Ollama for a refined attempt.
    """
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
    if diagnosis.get("missing_modules"):
        error_details += "\n## Missing Optional Modules\n"
        for mod in diagnosis["missing_modules"]:
            error_details += f"- `{mod}`\n"

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
{"- The test has errors on both versions. Fix syntax/import/assertion errors first, then ensure it targets the bug." if diagnosis['status'] in ('FAIL_TO_FAIL', 'FAIL_TO_FAIL_ENV', 'FAIL_TO_FAIL_IMPORT') else ""}
{"- The assertions are backwards. The test should expect CORRECT behavior (which fails on buggy code)." if diagnosis['status'] == 'PASS_TO_FAIL' else ""}
{"- Avoid hard dependency on missing optional modules; target core code paths or mock optional modules safely." if diagnosis.get('missing_modules') else ""}
{"- Start from scratch with a new minimal test (do not preserve previous structure)." if force_rewrite else ""}

### Rules:
1. Analyze the error output carefully — fix the SPECIFIC errors shown.
2. Make sure imports are correct for the project.
2b. If optional modules are missing, avoid importing optional integration internals directly.
3. Use the EXACT function/class/method names from the source code.
4. The test should assert the CORRECT (fixed) behavior, so it fails when the bug is present.
5. Return ONLY raw test file code (no markdown fences).
6. Include a comment at the top with the suggested file path.
7. If you use pytest decorators or helpers, include `import pytest`.
"""

    print(f"  🤖 Calling Ollama API (attempt {attempt + 1})...")
    last_err = ""
    for regen_try in range(2):
        system_prompt = (
            "You are an expert test engineer specializing in regression tests. "
            "You are fixing a test that failed verification. Be precise about "
            "imports, function names, and assertions. Analyze the error output carefully."
        )
        data = _ollama_chat(messages=[{"role": "user", "content": prompt}], system_prompt=system_prompt)
        text = data.get("message", {}).get("content", "")
        candidate = _normalize_python_test_code(_sanitize_test_code(text))

        if suggested_path and str(suggested_path).endswith(".py"):
            ok, err = _is_valid_python(candidate)
            if ok:
                bad, reason = _has_repeated_bad_pattern(candidate, diagnosis)
                if not bad:
                    if candidate.strip() == (previous_test_code or "").strip():
                        last_err = "regenerated code unchanged"
                        prompt += (
                            "\n\nYour last output was effectively unchanged. "
                            "Rewrite from scratch with a different strategy."
                        )
                        continue
                    return candidate
                last_err = reason
                prompt += (
                    f"\n\nYour last output repeated a failing pattern ({reason}). "
                    "Do not reuse that assertion strategy."
                )
                continue
            last_err = err
            prompt += (
                f"\n\nYour last output had Python syntax errors ({err}). "
                "Return ONLY valid Python code, no prose, no markdown fences."
            )
            continue
        return candidate

    raise RuntimeError(f"Regenerated test is not valid Python after retries: {last_err}")


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

        if str(suggested_path).endswith(".py") and current_test_source.exists():
            existing = current_test_source.read_text()
            normalized = _normalize_python_test_code(existing)
            if normalized != existing:
                current_test_source.write_text(normalized)

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
        missing_modules = _extract_missing_modules(pre_result, post_result)
        shimmable_missing = _filter_shimmable_modules(missing_modules, clone_dir)
        preflight_failed = bool(
            pre_result.get("preflight_collect_failed") or
            post_result.get("preflight_collect_failed")
        )
        if status == "FAIL_TO_FAIL" and (missing_modules or preflight_failed):
            status = "FAIL_TO_FAIL_IMPORT"

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
            "missing_modules": missing_modules,
            "shimmable_missing_modules": shimmable_missing,
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

        if status == "FAIL_TO_FAIL_IMPORT" and str(suggested_path).endswith(".py") and shimmable_missing:
            shimmed_test = _inject_missing_module_shims(current_test_code, shimmable_missing)
            shimmed_test = _normalize_python_test_code(shimmed_test)
            if shimmed_test != current_test_code:
                shim_path = test_source.parent / f"test_issue_{issue}_attempt{attempt + 1}_shim.py"
                shim_path.write_text(shimmed_test)
                current_test_source = shim_path
                print(f"  🩹 Injected optional-dependency shims: {', '.join(shimmable_missing)}")
                print(f"  ↩ Retrying with shimmed test: {shim_path.name}")
                continue

        # ── Diagnose & Regenerate ──
        print(f"\n  ⚠️  Status: {status} — diagnosing and regenerating...")
        diagnosis = diagnose_failure(status, pre_result, post_result, current_test_code)

        try:
            new_test_code = regenerate_test(
                bug_data,
                current_test_code,
                diagnosis,
                attempt,
                suggested_path=suggested_path,
                force_rewrite=_should_force_strict_mode(attempt_history),
            )
            if str(suggested_path).endswith(".py") and shimmable_missing:
                new_test_code = _inject_missing_module_shims(new_test_code, shimmable_missing)
            if str(suggested_path).endswith(".py"):
                new_test_code = _normalize_python_test_code(new_test_code)

            # Save regenerated test to a new file
            regen_path = test_source.parent / f"test_issue_{issue}_attempt{attempt + 1}.py"
            regen_path.write_text(new_test_code)
            current_test_source = regen_path
            print(f"  📝 Regenerated test saved: {regen_path.name}")

        except Exception as e:
            print(f"  ❌ Regeneration failed: {e}")
            continue

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
