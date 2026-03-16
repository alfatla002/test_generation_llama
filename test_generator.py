# test_generator.py
import json
import os
from pathlib import Path
import re

import requests

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "4096"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "600"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))


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
    # Preserve order while deduplicating.
    return list(dict.fromkeys(urls))


def _ollama_chat(messages, system_prompt):
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "options": {
            "temperature": OLLAMA_TEMPERATURE,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": OLLAMA_MAX_TOKENS,
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


def build_prompt(bug_data):
    """Build a prompt for Claude to generate a regression test."""

    files_section = ""
    for f in bug_data["files"]:
        files_section += f"""
### File: `{f['filename']}`

**Diff (patch):**
```diff
{f['patch']}
```

**Buggy version (before fix):**
```
{(f['before_fix'] or 'N/A')[:3000]}
```

**Fixed version (after fix):**
```
{(f['after_fix'] or 'N/A')[:3000]}
```
"""

    return f"""You are a senior test engineer. Your task is to write a regression test 
that **fails on the buggy code** and **passes on the fixed code**.

## Bug Report (GitHub Issue #{bug_data['issue_number']})
**Title:** {bug_data['issue_title']}
**Description:**
{bug_data['issue_body'][:2000]}

## Fix (PR #{bug_data['pr_number']})
**Title:** {bug_data['pr_title']}
**Description:**
{bug_data['pr_body'][:2000]}

## Changed Files
{files_section}

## Instructions
1. Analyze the bug and the fix carefully.
2. Write a test that exercises the EXACT buggy behavior.
3. The test MUST:
   - **FAIL** when run against the buggy (pre-fix) code
   - **PASS** when run against the fixed (post-fix) code
4. Use the project's existing test framework (detect from file extensions/imports).
5. Include clear test names describing what bug is being caught.
6. Return ONLY the test file content, no explanation.
7. At the top, add a comment with the suggested file path for the test.

Return the test as a single code block.
"""


def generate_test(bug_data):
    """Call Ollama API to generate a test."""
    prompt = build_prompt(bug_data)
    data = _ollama_chat(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="You are an expert test engineer. Generate precise regression tests.",
    )

    text = data.get("message", {}).get("content", "")
    return _sanitize_test_code(text)


def generate_all_tests(bugs_file="collected_bugs.json", output_dir="generated_tests"):
    """Generate tests for all collected bugs."""
    bugs = json.loads(Path(bugs_file).read_text())
    output = Path(output_dir)
    output.mkdir(exist_ok=True)

    results = []
    for bug in bugs:
        print(f"Generating test for issue #{bug['issue_number']}...")
        try:
            test_code = generate_test(bug)

            # Extract suggested filepath from comment
            filepath_match = re.search(
                r'#\s*(?:File|Path|Suggested path):\s*(.+)',
                test_code, re.IGNORECASE
            )
            if filepath_match:
                suggested_path = filepath_match.group(1).strip()
            else:
                suggested_path = f"tests/test_issue_{bug['issue_number']}.py"

            # Save test
            test_file = output / f"test_issue_{bug['issue_number']}.py"
            test_file.write_text(test_code)

            results.append({
                "issue_number": bug["issue_number"],
                "test_file": str(test_file),
                "suggested_path": suggested_path,
                "pre_fix_sha": bug["pre_fix_sha"],
                "merge_commit_sha": bug["merge_commit_sha"],
            })
            print(f"  ✓ Generated: {test_file}")

        except Exception as e:
            print(f"  ✗ Failed: {e}")

    Path(output / "manifest.json").write_text(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    generate_all_tests()
