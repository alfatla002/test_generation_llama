#!/usr/bin/env python3
"""
Unit Test Generation from GitHub Issues
Generates tests with CoT, validates fail-to-pass workflow
"""

import os
import json
import tempfile
import shutil
import subprocess
from typing import Dict, List, Optional
from dataclasses import dataclass

# Check anthropic package version
try:
    from anthropic import Anthropic
    import anthropic
    
    # Check if we have the new API
    try:
        _test_client = Anthropic(api_key="test")
        if not hasattr(_test_client, 'messages'):
            raise AttributeError("Old anthropic version")
    except AttributeError:
        print("\n❌ ERROR: Outdated 'anthropic' package")
        print("\n🔧 Please upgrade:")
        print("   pip install --upgrade anthropic")
        print("\n   Required version: >= 0.18.0")
        
        # Try to show current version
        try:
            print(f"   Current version: {anthropic.__version__}")
        except:
            print("   Current version: Unknown (very old)")
        
        print("\n📚 Then try again!")
        exit(1)
        
except ImportError:
    print("\n❌ ERROR: 'anthropic' package not installed")
    print("\n🔧 Please install:")
    print("   pip install anthropic")
    print("\n📚 Then try again!")
    exit(1)


@dataclass
class TestResult:
    passed: bool
    output: str
    error: str = ""
    test_code: str = ""


class IssueToTestGenerator:
    """Generate unit tests from GitHub issues with fail-to-pass validation"""
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    def generate_test_with_cot(self, issue_data: Dict) -> Dict[str, str]:
        """Generate test with chain of thought reasoning"""
        
        buggy_file = issue_data['code_context']['buggy_files'][0]
        language = issue_data['metadata']['language']
        test_framework = issue_data['metadata']['test_framework']
        
        prompt = f"""You are a senior software engineer writing a unit test for this GitHub issue.

**Issue #{issue_data['metadata']['issue_number']}**: {issue_data['issue']['title']}

**Issue Description**:
{issue_data['issue']['description'][:500]}

**Current Buggy Code**:
File: {buggy_file['path']}
```{language}
{buggy_file['content'][:1000]}
```

**Test Framework**: {test_framework}
**Language**: {language}

Your task: Generate a unit test that will FAIL with the current buggy code but should PASS after the bug is fixed.

Use this structure:

<chain_of_thought>
1. What is the bug based on the issue description and code?
2. What specific behavior should the test verify?
3. What assertion will fail with the buggy code?
4. Why will this test pass after the fix?
5. What edge cases should be tested?
</chain_of_thought>

<test_code>
```{language}
# Complete, runnable test code with all imports
{self._get_test_template(language, test_framework)}
```
</test_code>

IMPORTANT: 
- Include ALL necessary imports
- Test should be complete and runnable
- Focus on the specific bug mentioned in the issue
- Keep test simple and focused
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = response.content[0].text
        
        return {
            "chain_of_thought": self._extract_between_tags(content, "chain_of_thought"),
            "test_code": self._extract_code_block(content, "test_code"),
            "full_response": content
        }
    
    def generate_fix_with_cot(self, issue_data: Dict, test_code: str, 
                             test_failure: str) -> Dict[str, str]:
        """Generate fix with chain of thought reasoning"""
        
        buggy_file = issue_data['code_context']['buggy_files'][0]
        language = issue_data['metadata']['language']
        
        prompt = f"""You are a senior software engineer fixing a bug.

**Issue**: {issue_data['issue']['title']}

**Issue Description**:
{issue_data['issue']['description'][:500]}

**Current Buggy Code**:
```{language}
{buggy_file['content'][:1000]}
```

**Failing Test**:
```{language}
{test_code}
```

**Test Failure Output**:
```
{test_failure[:500]}
```

Your task: Fix the buggy code so the test passes.

Use this structure:

<chain_of_thought>
1. Root cause analysis: What exactly is causing the test to fail?
2. What needs to change in the code?
3. How does the fix address the test failure?
4. What edge cases does the fix handle?
5. Are there any potential side effects?
</chain_of_thought>

<fixed_code>
```{language}
# Complete fixed function/class/file
# Include the ENTIRE fixed version, not just the changed lines
```
</fixed_code>

IMPORTANT:
- Provide the COMPLETE fixed code, not just a patch
- Fix should be minimal but complete
- Ensure the fix addresses the root cause
"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = response.content[0].text
        
        return {
            "chain_of_thought": self._extract_between_tags(content, "chain_of_thought"),
            "fixed_code": self._extract_code_block(content, "fixed_code"),
            "full_response": content
        }
    
    def run_test_in_sandbox(self, issue_data: Dict, test_code: str, 
                           use_fixed_code: bool = False) -> TestResult:
        """Run test in isolated sandbox environment"""
        
        language = issue_data['metadata']['language']
        buggy_file = issue_data['code_context']['buggy_files'][0]
        
        # Create temporary directory for testing
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create source file
            source_path = os.path.join(tmpdir, buggy_file['path'])
            os.makedirs(os.path.dirname(source_path), exist_ok=True)
            
            # Write source code (buggy or fixed)
            if use_fixed_code and 'fixed_code' in issue_data.get('fix', {}):
                source_content = issue_data['fix']['fixed_code']
            else:
                source_content = buggy_file['content']
            
            with open(source_path, 'w', encoding='utf-8') as f:
                f.write(source_content)
            
            # Create test file
            test_path = os.path.join(tmpdir, issue_data['expected_test']['path'])
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            
            with open(test_path, 'w', encoding='utf-8') as f:
                f.write(test_code)
            
            # Run test based on language
            try:
                result = self._run_test_for_language(
                    tmpdir, 
                    test_path,
                    language,
                    issue_data['metadata']['test_framework']
                )
                
                return TestResult(
                    passed=result.returncode == 0,
                    output=result.stdout,
                    error=result.stderr if result.stderr else result.stdout,
                    test_code=test_code
                )
            
            except Exception as e:
                return TestResult(
                    passed=False,
                    output="",
                    error=f"Test execution failed: {str(e)}",
                    test_code=test_code
                )
    
    def _run_test_for_language(self, working_dir: str, test_path: str, 
                              language: str, framework: str) -> subprocess.CompletedProcess:
        """Run test based on language and framework"""
        
        if language == "python":
            if framework == "pytest":
                cmd = ["pytest", test_path, "-v", "--tb=short"]
            else:  # unittest
                cmd = ["python", "-m", "unittest", test_path.replace('/', '.').replace('.py', '')]
        
        elif language in ["javascript", "typescript"]:
            if framework == "jest":
                cmd = ["npx", "jest", test_path]
            else:  # mocha
                cmd = ["npx", "mocha", test_path]
        
        elif language == "java":
            # Simplified - would need proper classpath setup
            cmd = ["javac", test_path]
        
        else:
            raise ValueError(f"Unsupported language: {language}")
        
        return subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30
        )
    
    def _get_test_template(self, language: str, framework: str) -> str:
        """Get test template based on language"""
        
        templates = {
            "python": """import pytest
from src.module import function_name

def test_bug_fix():
    # Your test here
    assert expected == actual
""",
            "javascript": """const { functionName } = require('./module');

test('bug fix', () => {
    expect(functionName()).toBe(expected);
});
""",
        }
        
        return templates.get(language, "# Test code here")
    
    def _extract_between_tags(self, text: str, tag: str) -> str:
        """Extract content between XML tags"""
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        
        start = text.find(start_tag)
        end = text.find(end_tag)
        
        if start == -1 or end == -1:
            return text
        
        return text[start + len(start_tag):end].strip()
    
    def _extract_code_block(self, text: str, tag: str) -> str:
        """Extract code from markdown code blocks within tags"""
        content = self._extract_between_tags(text, tag)
        
        # Remove markdown code fences
        if "```" in content:
            lines = content.split('\n')
            in_code = False
            code_lines = []
            
            for line in lines:
                if line.strip().startswith("```"):
                    in_code = not in_code
                    continue
                if in_code:
                    code_lines.append(line)
            
            return '\n'.join(code_lines)
        
        return content


def run_test_generation_workflow(
    dataset_file: str,
    api_key: str,
    output_dir: str = "test_generation_results",
    max_issues: int = None,
    skip_execution: bool = False
):
    """Run complete test generation workflow on collected issues
    
    Args:
        dataset_file: Path to JSON dataset
        api_key: Anthropic API key
        output_dir: Output directory for results
        max_issues: Limit number of issues to process
        skip_execution: If True, only generate tests without running them
    """
    
    print(f"🚀 Starting Test Generation Workflow")
    print(f"{'='*70}\n")
    
    # Load dataset
    print(f"📂 Loading dataset from {dataset_file}...")
    
    try:
        # Explicitly use UTF-8 encoding (important on Windows)
        with open(dataset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in {dataset_file}")
        print(f"   {str(e)}")
        print(f"\n💡 Troubleshooting:")
        print(f"   1. Check if file was created properly")
        print(f"   2. Try: python -m json.tool {dataset_file}")
        print(f"   3. Re-run data collection if needed")
        return []
    except FileNotFoundError:
        print(f"❌ Error: File not found: {dataset_file}")
        print(f"\n💡 Make sure you've collected issues first:")
        print(f"   python collect_issues.py owner/repo")
        return []
    except Exception as e:
        print(f"❌ Error loading dataset: {str(e)}")
        return []
    
    issues = data.get('dataset', [])
    
    if not issues:
        print("❌ No issues found in dataset")
        return
    
    if max_issues:
        issues = issues[:max_issues]
    
    print(f"   Found {len(issues)} issues to process\n")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize generator
    generator = IssueToTestGenerator(api_key)
    
    # Process each issue
    results = []
    
    for idx, issue in enumerate(issues, 1):
        print(f"\n{'='*70}")
        print(f"[{idx}/{len(issues)}] Processing Issue #{issue['metadata']['issue_number']}")
        print(f"Title: {issue['issue']['title']}")
        print(f"Repo: {issue['metadata']['repo']}")
        print(f"{'='*70}\n")
        
        result = {
            "issue_id": issue['id'],
            "issue_number": issue['metadata']['issue_number'],
            "title": issue['issue']['title'],
            "repo": issue['metadata']['repo'],
            "success": False,
            "steps": {}
        }
        
        try:
            # STEP 1: Generate test
            print("📝 [STEP 1/5] Generating test with Chain of Thought...")
            test_result = generator.generate_test_with_cot(issue)
            
            print("\n🧠 Chain of Thought (excerpt):")
            print("-" * 70)
            cot_preview = test_result['chain_of_thought'][:300]
            print(cot_preview + "..." if len(test_result['chain_of_thought']) > 300 else cot_preview)
            print("-" * 70)
            
            print(f"\n✅ Generated test ({len(test_result['test_code'])} chars)")
            
            result['steps']['test_generation'] = {
                "success": True,
                "cot": test_result['chain_of_thought'],
                "code": test_result['test_code']
            }
            
            # STEP 2: Run test on buggy code (should FAIL)
            # if not skip_execution:
            #     print("\n🔴 [STEP 2/5] Running test on BUGGY code (expecting FAILURE)...")
                
            #     initial_result = generator.run_test_in_sandbox(
            #         issue,
            #         test_result['test_code'],
            #         use_fixed_code=False
            #     )
                
            #     if not initial_result.passed:
            #         print(f"   ❌ Test FAILED as expected ✓")
            #         print(f"   Error: {initial_result.error[:150]}...")
            #     else:
            #         print(f"   ⚠️  Test PASSED (unexpected - test may not catch the bug)")
                
            #     result['steps']['initial_test'] = {
            #         "passed": initial_result.passed,
            #         "expected_failure": True,
            #     "output": initial_result.output[:500],
            #     "error": initial_result.error[:500]
            # }
            # else:
                # print("\n⏭️  [STEP 2/5] Skipping test execution (generation-only mode)")
                # result['steps']['initial_test'] = {
                #     "passed": None,
                #     "expected_failure": True,
                #     "output": "Test execution skipped",
                #     "error": "Execution skipped - generation-only mode"
                # }
                # initial_result = TestResult(
                #     passed=False,
                #     output="",
                #     error="Mock error for generation-only mode"
                # )
            
            # STEP 3: Generate fix
            print("\n🔧 [STEP 3/5] Generating fix with Chain of Thought...")
            
            fix_result = generator.generate_fix_with_cot(
                issue,
                test_result['test_code'],
                initial_result.error
            )
            
            print("\n🧠 Fix Chain of Thought (excerpt):")
            print("-" * 70)
            fix_cot_preview = fix_result['chain_of_thought'][:300]
            print(fix_cot_preview + "..." if len(fix_result['chain_of_thought']) > 300 else fix_cot_preview)
            print("-" * 70)
            
            print(f"\n✅ Generated fix ({len(fix_result['fixed_code'])} chars)")
            
            result['steps']['fix_generation'] = {
                "success": True,
                "cot": fix_result['chain_of_thought'],
                "code": fix_result['fixed_code']
            }
            
            # STEP 4: Apply fix to issue data
            print("\n🔨 [STEP 4/5] Preparing fixed code for testing...")
            issue['fix']['fixed_code'] = fix_result['fixed_code']
            
            # STEP 5: Run test on fixed code (should PASS)
            if not skip_execution:
                print("\n🟢 [STEP 5/5] Running test on FIXED code (expecting SUCCESS)...")
                
                final_result = generator.run_test_in_sandbox(
                    issue,
                    test_result['test_code'],
                    use_fixed_code=True
                )
                
                if final_result.passed:
                    print(f"   ✅ Test PASSED as expected ✓")
                else:
                    print(f"   ❌ Test FAILED (fix may be incorrect)")
                    print(f"   Error: {final_result.error[:150]}...")
                
                result['steps']['final_test'] = {
                    "passed": final_result.passed,
                    "expected_pass": True,
                    "output": final_result.output[:500]
                }
                
                # Determine overall success
                workflow_success = (not initial_result.passed) and final_result.passed
            else:
                print("\n⏭️  [STEP 5/5] Skipping test execution (generation-only mode)")
                result['steps']['final_test'] = {
                    "passed": None,
                    "expected_pass": True,
                    "output": "Test execution skipped"
                }
                
                # In skip mode, success = test and fix were generated
                workflow_success = True
            
            result['success'] = workflow_success
            
            if workflow_success:
                if skip_execution:
                    print("\n🎉 GENERATION SUCCESSFUL!")
                    print("   ✓ Test code generated")
                    print("   ✓ Fix code generated")
                else:
                    print("\n🎉 WORKFLOW SUCCESSFUL!")
                    print("   ✓ Test failed on buggy code")
                    print("   ✓ Test passed on fixed code")
            else:
                print("\n⚠️  WORKFLOW INCOMPLETE:")
                if not skip_execution and initial_result.passed:
                    print("   ✗ Test didn't fail on buggy code")
                if not final_result.passed:
                    print("   ✗ Test didn't pass on fixed code")
            
            # Save individual result
            result_file = os.path.join(output_dir, f"issue_{issue['metadata']['issue_number']}_result.json")
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
            
        except Exception as e:
            print(f"\n❌ ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            result['error'] = str(e)
        
        results.append(result)
    
    # Generate summary
    print(f"\n{'='*70}")
    print("WORKFLOW SUMMARY")
    print(f"{'='*70}")
    
    total = len(results)
    successful = sum(1 for r in results if r.get('success', False))
    test_gen = sum(1 for r in results if 'test_generation' in r.get('steps', {}))
    initial_fail = sum(1 for r in results if not r.get('steps', {}).get('initial_test', {}).get('passed', True))
    fix_gen = sum(1 for r in results if 'fix_generation' in r.get('steps', {}))
    final_pass = sum(1 for r in results if r.get('steps', {}).get('final_test', {}).get('passed', False))
    
    print(f"\nTotal issues processed: {total}")
    print(f"Complete workflows: {successful}/{total} ({successful/total*100:.1f}%)")
    print(f"\nBreakdown:")
    print(f"  - Tests generated: {test_gen}/{total}")
    print(f"  - Tests failed on buggy code: {initial_fail}/{total}")
    print(f"  - Fixes generated: {fix_gen}/{total}")
    print(f"  - Tests passed on fixed code: {final_pass}/{total}")
    
    # Save summary
    summary_file = os.path.join(output_dir, "summary.json")
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total": total,
                "successful": successful,
                "success_rate": successful/total if total > 0 else 0,
                "tests_generated": test_gen,
                "initial_failures": initial_fail,
                "fixes_generated": fix_gen,
                "final_passes": final_pass
            },
            "results": results
        }, f, indent=2)
    
    print(f"\n📊 Results saved to: {output_dir}/")
    print(f"   - Individual results: issue_*_result.json")
    print(f"   - Summary: summary.json")
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate unit tests from GitHub issues with fail-to-pass validation"
    )
    parser.add_argument(
        "dataset",
        help="Path to GitHub issues dataset JSON file"
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)",
        default=os.getenv("ANTHROPIC_API_KEY")
    )
    parser.add_argument(
        "--output",
        default="test_generation_results",
        help="Output directory for results (default: test_generation_results)"
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        help="Maximum issues to process (for testing)"
    )
    parser.add_argument(
        "--skip-execution",
        action="store_true",
        help="Skip test execution, only generate test and fix code (useful on Windows)"
    )
    
    args = parser.parse_args()
    
    if not args.api_key:
        print("❌ Error: Anthropic API key required")
        print("Set ANTHROPIC_API_KEY environment variable or use --api-key")
        return 1
    
    if not os.path.exists(args.dataset):
        print(f"❌ Error: Dataset file not found: {args.dataset}")
        return 1
    
    # Run workflow
    run_test_generation_workflow(
        dataset_file=args.dataset,
        api_key=args.api_key,
        output_dir=args.output,
        max_issues=args.max_issues,
        skip_execution=args.skip_execution
    )
    
    return 0


if __name__ == "__main__":
    exit(main())
