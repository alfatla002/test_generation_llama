# test_generator.py
import anthropic
import json
from pathlib import Path
import re


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
    """Call Claude API to generate a test."""
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var

    prompt = build_prompt(bug_data)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        system="You are an expert test engineer. Generate precise regression tests."
    )

    # Extract code block from response
    text = response.content[0].text
    # Try to extract code from markdown block
    
    code_match = re.search(r'```[\w]*\n(.*?)```', text, re.DOTALL)
    if code_match:
        return code_match.group(1)
    return text


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