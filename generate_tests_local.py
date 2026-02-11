#!/usr/bin/env python3
"""
Local Repository Test Generation
Generates tests and validates against actual GitHub commits
"""

import os
import json
import subprocess
import shutil
import tempfile
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

# Check anthropic package
try:
    from anthropic import Anthropic
    import anthropic
    
    try:
        _test_client = Anthropic(api_key="test")
        if not hasattr(_test_client, 'messages'):
            raise AttributeError("Old anthropic version")
    except AttributeError:
        print("\n❌ ERROR: Outdated 'anthropic' package")
        print("\n🔧 Please upgrade:")
        print("   pip install --upgrade anthropic")
        print("\n   Required version: >= 0.18.0")
        try:
            print(f"   Current version: {anthropic.__version__}")
        except:
            print("   Current version: Unknown")
        print("\n📚 Then try again!")
        exit(1)
except ImportError:
    print("\n❌ ERROR: 'anthropic' package not installed")
    print("\n🔧 Please install:")
    print("   pip install anthropic")
    exit(1)


@dataclass
class TestResult:
    passed: bool
    output: str
    error: str = ""
    commit: str = ""


class LocalRepoTestGenerator:
    """Generate and validate tests against local repository commits"""
    
    def __init__(self, api_key: str, work_dir: str = "./test_repos", 
                 model: str = "claude-sonnet-4-20250514"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(exist_ok=True)
    
    def clone_or_update_repo(self, repo_full_name: str) -> Path:
        """Clone repository if needed, or pull latest"""
        
        repo_name = repo_full_name.split('/')[-1]
        repo_path = self.work_dir / repo_name
        
        if repo_path.exists():
            print(f"   📂 Repository already exists: {repo_path}")
            return repo_path
        
        print(f"   📥 Cloning {repo_full_name}...")
        
        clone_url = f"https://github.com/{repo_full_name}.git"
        
        try:
            result = subprocess.run(
                ['git', 'clone', clone_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            
            if result.returncode != 0:
                raise Exception(f"Clone failed: {result.stderr}")
            
            print(f"   ✅ Cloned successfully")
            return repo_path
            
        except subprocess.TimeoutExpired:
            raise Exception("Clone timeout (>5 minutes)")
        except FileNotFoundError:
            raise Exception("git command not found. Please install Git.")
    
    def checkout_commit(self, repo_path: Path, commit_sha: str) -> bool:
        """Checkout specific commit"""
        
        try:
            # Fetch if needed
            subprocess.run(
                ['git', 'fetch', 'origin'],
                cwd=repo_path,
                capture_output=True,
                timeout=60
            )
            
            # Checkout
            result = subprocess.run(
                ['git', 'checkout', commit_sha],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                print(f"   ⚠️  Checkout failed: {result.stderr}")
                return False
            
            return True
            
        except Exception as e:
            print(f"   ⚠️  Checkout error: {e}")
            return False
    
    def get_buggy_commit(self, issue_data: Dict) -> Optional[str]:
        """Extract buggy commit SHA from issue data"""
        
        # Try to get parent commit of PR (before fix)
        code_context = issue_data.get('code_context', {})
        buggy_files = code_context.get('buggy_files', [])
        
        if buggy_files and 'commit' in buggy_files[0]:
            return buggy_files[0]['commit']
        
        # Fallback: use metadata
        metadata = issue_data.get('metadata', {})
        return metadata.get('buggy_commit')
    
    def get_fixed_commit(self, issue_data: Dict) -> Optional[str]:
        """Extract fixed commit SHA from issue data"""
        
        # Get merge commit from PR
        fix_data = issue_data.get('fix', {})
        if 'merge_commit_sha' in fix_data:
            return fix_data['merge_commit_sha']
        
        # Fallback
        metadata = issue_data.get('metadata', {})
        return metadata.get('fixed_commit')
    
    def generate_test_with_cot(self, issue_data: Dict) -> Dict[str, str]:
        """Generate test with chain of thought reasoning"""
        
        buggy_file = issue_data['code_context']['buggy_files'][0]
        language = issue_data['metadata']['language']
        test_framework = issue_data['metadata'].get('test_framework', 'pytest')
        
        prompt = f"""You are a senior software engineer writing a unit test for this GitHub issue.

**Issue #{issue_data['metadata']['issue_number']}**: {issue_data['issue']['title']}

**Issue Description**:
{issue_data['issue']['description'][:500]}

**Buggy Code**:
File: {buggy_file['path']}
```{language}
{buggy_file['content'][:1500]}
```

**Your Task**:
1. Analyze the bug described in the issue
2. Write a focused unit test that will FAIL on the buggy code and PASS after the fix
3. The test will run in the actual repository, so use proper imports

**Output Format**:
<chain_of_thought>
1. What is the bug?
2. What behavior should the test verify?
3. What assertion will fail on buggy code?
4. Why will this test pass after the fix?
5. What edge cases should be covered?
</chain_of_thought>

<test_code>
```{language}
# Complete test using {test_framework}
# Include ALL necessary imports
# Test should be runnable in the repository
```
</test_code>

**Guidelines**:
- Test should be in a new file (e.g., test_issue_1234.py)
- Include all imports needed
- Focus on the specific bug
- Keep test simple and clear
- Test should work in the repository context
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
    
    def _extract_between_tags(self, text: str, tag: str) -> str:
        """Extract content between XML tags"""
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        
        start = text.find(start_tag)
        end = text.find(end_tag)
        
        if start != -1 and end != -1:
            return text[start + len(start_tag):end].strip()
        return ""
    
    def _extract_code_block(self, text: str, tag: str) -> str:
        """Extract code from markdown code block inside XML tags"""
        content = self._extract_between_tags(text, tag)
        
        # Remove markdown code fences
        lines = content.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        
        return '\n'.join(lines).strip()
    
    def run_test_on_commit(self, repo_path: Path, test_code: str, 
                          commit_sha: str, language: str,
                          test_framework: str = 'pytest') -> TestResult:
        """Run test on specific commit"""
        
        # Checkout commit
        print(f"      Checking out commit {commit_sha[:8]}...")
        if not self.checkout_commit(repo_path, commit_sha):
            return TestResult(
                passed=False,
                output="",
                error=f"Failed to checkout commit {commit_sha}",
                commit=commit_sha
            )
        
        # Create test file
        test_file = repo_path / f"test_generated_{commit_sha[:8]}.py"
        
        try:
            with open(test_file, 'w', encoding='utf-8') as f:
                f.write(test_code)
            
            # Run test based on language and framework
            if language == 'python':
                result = self._run_pytest(repo_path, test_file)
            elif language == 'javascript' or language == 'typescript':
                result = self._run_jest(repo_path, test_file)
            else:
                result = TestResult(
                    passed=False,
                    output="",
                    error=f"Unsupported language: {language}",
                    commit=commit_sha
                )
            
            result.commit = commit_sha
            return result
            
        finally:
            # Cleanup test file
            if test_file.exists():
                test_file.unlink()
    
    def _run_pytest(self, repo_path: Path, test_file: Path) -> TestResult:
        """Run pytest on test file"""
        
        try:
            result = subprocess.run(
                ['pytest', str(test_file), '-v', '--tb=short'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            passed = result.returncode == 0
            
            return TestResult(
                passed=passed,
                output=result.stdout,
                error=result.stderr if not passed else ""
            )
            
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output="",
                error="Test timeout (>60s)"
            )
        except FileNotFoundError:
            return TestResult(
                passed=False,
                output="",
                error="pytest not found. Install: pip install pytest"
            )
    
    def _run_jest(self, repo_path: Path, test_file: Path) -> TestResult:
        """Run jest on test file"""
        
        try:
            result = subprocess.run(
                ['npm', 'test', str(test_file)],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            passed = result.returncode == 0
            
            return TestResult(
                passed=passed,
                output=result.stdout,
                error=result.stderr if not passed else ""
            )
            
        except subprocess.TimeoutExpired:
            return TestResult(passed=False, output="", error="Test timeout")
        except FileNotFoundError:
            return TestResult(passed=False, output="", error="npm not found")


def run_local_test_generation(
    dataset_file: str,
    api_key: str,
    output_dir: str = "local_test_results",
    work_dir: str = "./test_repos",
    max_issues: Optional[int] = None
):
    """Run test generation and validation on local repository"""
    
    print(f"🚀 Local Repository Test Generation")
    print(f"{'='*70}\n")
    
    # Load dataset
    print(f"📂 Loading dataset from {dataset_file}...")
    
    try:
        with open(dataset_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Error loading dataset: {e}")
        return []
    
    issues = data.get('dataset', [])
    
    if max_issues:
        issues = issues[:max_issues]
    
    print(f"✅ Found {len(issues)} issues\n")
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize generator
    generator = LocalRepoTestGenerator(api_key, work_dir)
    
    results = []
    
    for i, issue in enumerate(issues, 1):
        print(f"\n{'='*70}")
        print(f"[{i}/{len(issues)}] Processing Issue #{issue['metadata']['issue_number']}: {issue['issue']['title'][:50]}...")
        print(f"{'='*70}")
        
        result = {
            "issue_id": issue['id'],
            "issue_number": issue['metadata']['issue_number'],
            "title": issue['issue']['title'],
            "repo": issue['metadata']['repo'],
            "success": False,
            "steps": {}
        }
        
        try:
            repo_name = issue['metadata']['repo']
            
            # STEP 1: Clone/Update repository
            print(f"\n📥 [STEP 1/5] Setting up repository...")
            repo_path = generator.clone_or_update_repo(repo_name)
            
            result['steps']['repo_setup'] = {
                "success": True,
                "repo_path": str(repo_path)
            }
            
            # STEP 2: Generate test
            print(f"\n📝 [STEP 2/5] Generating test with Chain of Thought...")
            
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
            
            # STEP 3: Get commit SHAs
            print(f"\n🔍 [STEP 3/5] Identifying commits...")
            
            buggy_commit = generator.get_buggy_commit(issue)
            fixed_commit = generator.get_fixed_commit(issue)
            
            if not buggy_commit or not fixed_commit:
                print(f"   ⚠️  Missing commit info:")
                print(f"      Buggy: {buggy_commit or 'NOT FOUND'}")
                print(f"      Fixed: {fixed_commit or 'NOT FOUND'}")
                result['steps']['commit_lookup'] = {
                    "success": False,
                    "error": "Missing commit information"
                }
                continue
            
            print(f"   ✅ Buggy commit: {buggy_commit[:8]}")
            print(f"   ✅ Fixed commit: {fixed_commit[:8]}")
            
            result['steps']['commit_lookup'] = {
                "success": True,
                "buggy_commit": buggy_commit,
                "fixed_commit": fixed_commit
            }
            
            # STEP 4: Run test on BUGGY commit (should FAIL)
            print(f"\n🔴 [STEP 4/5] Running test on BUGGY commit (expecting FAILURE)...")
            
            buggy_result = generator.run_test_on_commit(
                repo_path,
                test_result['test_code'],
                buggy_commit,
                issue['metadata']['language'],
                issue['metadata'].get('test_framework', 'pytest')
            )
            
            if not buggy_result.passed:
                print(f"      ❌ Test FAILED as expected ✓")
                print(f"      Error: {buggy_result.error[:150]}...")
            else:
                print(f"      ⚠️  Test PASSED (unexpected - test may not catch bug)")
            
            result['steps']['buggy_test'] = {
                "passed": buggy_result.passed,
                "expected_failure": True,
                "commit": buggy_result.commit,
                "output": buggy_result.output[:500],
                "error": buggy_result.error[:500]
            }
            
            # STEP 5: Run test on FIXED commit (should PASS)
            print(f"\n🟢 [STEP 5/5] Running test on FIXED commit (expecting SUCCESS)...")
            
            fixed_result = generator.run_test_on_commit(
                repo_path,
                test_result['test_code'],
                fixed_commit,
                issue['metadata']['language'],
                issue['metadata'].get('test_framework', 'pytest')
            )
            
            if fixed_result.passed:
                print(f"      ✅ Test PASSED as expected ✓")
            else:
                print(f"      ❌ Test FAILED (unexpected)")
                print(f"      Error: {fixed_result.error[:150]}...")
            
            result['steps']['fixed_test'] = {
                "passed": fixed_result.passed,
                "expected_pass": True,
                "commit": fixed_result.commit,
                "output": fixed_result.output[:500],
                "error": fixed_result.error[:500]
            }
            
            # Determine success
            workflow_success = (not buggy_result.passed) and fixed_result.passed
            result['success'] = workflow_success
            
            if workflow_success:
                print(f"\n🎉 VALIDATION SUCCESSFUL!")
                print(f"   ✓ Test failed on buggy commit ({buggy_commit[:8]})")
                print(f"   ✓ Test passed on fixed commit ({fixed_commit[:8]})")
            else:
                print(f"\n⚠️  VALIDATION INCOMPLETE:")
                if buggy_result.passed:
                    print(f"   ✗ Test didn't fail on buggy code")
                if not fixed_result.passed:
                    print(f"   ✗ Test didn't pass on fixed code")
        
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")
            import traceback
            result['error'] = str(e)
            result['traceback'] = traceback.format_exc()
        
        # Save individual result
        result_file = os.path.join(output_dir, f"issue_{issue['metadata']['issue_number']}_result.json")
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        results.append(result)
    
    # Generate summary
    print(f"\n{'='*70}")
    print("VALIDATION SUMMARY")
    print(f"{'='*70}")
    
    total = len(results)
    successful = sum(1 for r in results if r.get('success', False))
    test_gen = sum(1 for r in results if 'test_generation' in r.get('steps', {}))
    buggy_fail = sum(1 for r in results if not r.get('steps', {}).get('buggy_test', {}).get('passed', True))
    fixed_pass = sum(1 for r in results if r.get('steps', {}).get('fixed_test', {}).get('passed', False))
    
    print(f"\nTotal issues processed: {total}")
    # print(f"Complete validations: {successful}/{total} ({successful/total*100:.1f}%)")
    if total > 0:
        print(f"Success: {successful}/{total} ({successful/total*100:.1f}%)")
    else:
        print(f"Success: 0/0 (0.0%)")
    print(f"\nBreakdown:")
    print(f"  - Tests generated: {test_gen}/{total}")
    print(f"  - Tests failed on buggy commit: {buggy_fail}/{total}")
    print(f"  - Tests passed on fixed commit: {fixed_pass}/{total}")
    
    # Save summary
    summary_file = os.path.join(output_dir, "summary.json")
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total": total,
                "successful": successful,
                "success_rate": successful/total if total > 0 else 0,
                "tests_generated": test_gen,
                "buggy_failures": buggy_fail,
                "fixed_passes": fixed_pass
            },
            "results": results
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n📊 Results saved to: {output_dir}/")
    print(f"   - Individual results: issue_*_result.json")
    print(f"   - Summary: summary.json")
    print(f"\n📂 Repositories cloned to: {generator.work_dir}/")
    
    return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate and validate tests against local GitHub repositories"
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
        default="local_test_results",
        help="Output directory for results (default: local_test_results)"
    )
    parser.add_argument(
        "--work-dir",
        default="./test_repos",
        help="Directory for cloned repositories (default: ./test_repos)"
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        help="Maximum issues to process (for testing)"
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
    run_local_test_generation(
        dataset_file=args.dataset,
        api_key=args.api_key,
        output_dir=args.output,
        work_dir=args.work_dir,
        max_issues=args.max_issues
    )
    
    return 0


if __name__ == "__main__":
    exit(main())
