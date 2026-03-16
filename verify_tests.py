# verify_tests.py
import subprocess
import json
import shutil
from pathlib import Path


def run_command(cmd, cwd=None, timeout=120):
    """Run a shell command and return result."""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True, timeout=timeout
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:],  # truncate
        "stderr": result.stderr[-2000:],
    }


def install_project_dependencies(clone_dir):
    """Install project deps and ensure package imports resolve for tests."""
    clone_dir = Path(clone_dir)
    if (clone_dir / "package.json").exists():
        run_command("npm install --silent 2>/dev/null", cwd=clone_dir, timeout=180)
        return

    # Python projects often use pyproject.toml + src layout.
    if (clone_dir / "requirements.txt").exists():
        run_command("python -m pip install -r requirements.txt -q", cwd=clone_dir, timeout=240)
    if (clone_dir / "pyproject.toml").exists() or (clone_dir / "setup.py").exists():
        run_command("python -m pip install -e . -q", cwd=clone_dir, timeout=240)


def verify_single_test(repo_url, test_info, workspace="workspace"):
    """
    Verify a single test follows the fail-to-pass pattern:
    1. Clone repo → checkout pre-fix commit → add test → run (expect FAIL)
    2. Checkout fix commit → run test again (expect PASS)
    """
    issue = test_info["issue_number"]
    clone_dir = Path(workspace) / f"repo_issue_{issue}"
    test_source = Path(test_info["test_file"])
    suggested_path = test_info["suggested_path"]
    pre_fix_sha = test_info["pre_fix_sha"]
    fix_sha = test_info["merge_commit_sha"]

    print(f"\n{'='*60}")
    print(f"Verifying Issue #{issue}")
    print(f"  Pre-fix SHA:  {pre_fix_sha[:10]}")
    print(f"  Fix SHA:      {fix_sha[:10]}")
    print(f"{'='*60}")

    # ── Step 1: Clone the repo ──
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    print("📥 Cloning repository...")
    res = run_command(f"git clone --quiet {repo_url} {clone_dir}")
    if res["returncode"] != 0:
        return {"issue": issue, "status": "CLONE_FAILED", "error": res["stderr"]}

    # ── Step 2: Checkout pre-fix (buggy) commit ──
    print(f"🔀 Checking out pre-fix commit: {pre_fix_sha[:10]}...")
    res = run_command(f"git checkout {pre_fix_sha}", cwd=clone_dir)
    if res["returncode"] != 0:
        return {"issue": issue, "status": "CHECKOUT_FAILED", "error": res["stderr"]}

    # ── Step 3: Copy test into the repo ──
    test_dest = clone_dir / suggested_path
    test_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(test_source, test_dest)
    print(f"📄 Test placed at: {suggested_path}")

    # ── Step 4: Install dependencies (adapt per project) ──
    print("📦 Installing dependencies...")
    install_project_dependencies(clone_dir)

    # ── Step 5: Run test on BUGGY code (expect FAIL) ──
    print("🧪 Running test on BUGGY code (should FAIL)...")
    test_cmd = detect_test_command(clone_dir, suggested_path)
    pre_fix_result = run_command(test_cmd, cwd=clone_dir, timeout=120)
    pre_fix_passed = pre_fix_result["returncode"] == 0

    if pre_fix_passed:
        print("  ⚠️  Test PASSED on buggy code (unexpected!)")
    else:
        print("  ✓  Test FAILED on buggy code (expected)")

    # ── Step 6: Checkout fix commit ──
    print(f"🔀 Checking out fix commit: {fix_sha[:10]}...")
    # Stash the test file, checkout fix, restore test
    run_command(f"git stash push {suggested_path}", cwd=clone_dir)
    res = run_command(f"git checkout {fix_sha}", cwd=clone_dir)

    # Re-place the test file (it might have been overwritten)
    shutil.copy(test_source, test_dest)

    # Re-install deps if needed
    install_project_dependencies(clone_dir)

    # ── Step 7: Run test on FIXED code (expect PASS) ──
    print("🧪 Running test on FIXED code (should PASS)...")
    post_fix_result = run_command(test_cmd, cwd=clone_dir, timeout=120)
    post_fix_passed = post_fix_result["returncode"] == 0

    if post_fix_passed:
        print("  ✓  Test PASSED on fixed code (expected)")
    else:
        print("  ⚠️  Test FAILED on fixed code (unexpected!)")

    # ── Determine overall result ──
    if not pre_fix_passed and post_fix_passed:
        status = "FAIL_TO_PASS ✅"
    elif pre_fix_passed and post_fix_passed:
        status = "PASS_TO_PASS ❌ (test doesn't catch the bug)"
    elif not pre_fix_passed and not post_fix_passed:
        status = "FAIL_TO_FAIL ❌ (test broken on both)"
    else:
        status = "PASS_TO_FAIL ❌ (regression in fix?)"

    print(f"\n📊 Result: {status}")

    return {
        "issue": issue,
        "status": status,
        "pre_fix_failed": not pre_fix_passed,
        "post_fix_passed": post_fix_passed,
        "pre_fix_output": pre_fix_result,
        "post_fix_output": post_fix_result,
    }


def detect_test_command(repo_dir, test_path):
    """Auto-detect how to run tests based on project structure."""
    repo_dir = Path(repo_dir)

    if test_path.endswith(".py"):
        if (repo_dir / "pytest.ini").exists() or (repo_dir / "setup.cfg").exists():
            return f"python -m pytest {test_path} -v --tb=short"
        return f"python -m pytest {test_path} -v --tb=short"

    if test_path.endswith((".js", ".ts", ".jsx", ".tsx")):
        if (repo_dir / "jest.config.js").exists() or (repo_dir / "jest.config.ts").exists():
            return f"npx jest {test_path} --no-coverage"
        if (repo_dir / "vitest.config.ts").exists():
            return f"npx vitest run {test_path}"
        return f"npx jest {test_path} --no-coverage"

    if test_path.endswith(".go"):
        return f"go test -v -run . ./{Path(test_path).parent}"

    # Default fallback
    return f"python -m pytest {test_path} -v"


def verify_all(repo_url, manifest_path="generated_tests/manifest.json"):
    """Verify all generated tests."""
    manifest = json.loads(Path(manifest_path).read_text())
    results = []

    for test_info in manifest:
        try:
            result = verify_single_test(repo_url, test_info)
            results.append(result)
        except Exception as e:
            results.append({
                "issue": test_info["issue_number"],
                "status": f"ERROR: {e}"
            })

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = len(results)
    f2p = sum(1 for r in results if "FAIL_TO_PASS" in str(r.get("status", "")))
    print(f"  Total:         {total}")
    print(f"  Fail-to-Pass:  {f2p} ({f2p/total*100:.0f}%)" if total else "")

    for r in results:
        print(f"  Issue #{r['issue']}: {r['status']}")

    Path("verification_results.json").write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    import sys
    repo_url = sys.argv[1]  # e.g., "https://github.com/owner/repo.git"
    verify_all(repo_url)
