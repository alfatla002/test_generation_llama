#!/usr/bin/env python3
"""
GitHub Issue Collector for Test Generation
Collects closed bug issues with their fixes from GitHub repositories
"""

import os
import json
import time
import re
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime
from github import Github, GithubException
from github.Repository import Repository
from github.Issue import Issue
from github.PullRequest import PullRequest


@dataclass
class IssueData:
    """Structured issue data for test generation"""
    id: str
    metadata: Dict
    issue: Dict
    code_context: Dict
    fix: Dict
    expected_test: Dict


class GitHubIssueCollector:
    """Collect GitHub issues with their fixes for test generation"""
    
    def __init__(self, github_token: str, verbose: bool = True):
        """
        Initialize collector
        
        Args:
            github_token: GitHub personal access token
            verbose: Print progress messages
        """
        self.gh = Github(github_token)
        self.verbose = verbose
        self.rate_limit_warned = False
    
    def log(self, message: str):
        """Print message if verbose mode enabled"""
        if self.verbose:
            print(message)
    
    def check_rate_limit(self):
        """Check and warn about rate limits"""
        try:
            rate_limit = self.gh.get_rate_limit()
            
            # Try to access core rate limit (works in most PyGithub versions)
            try:
                remaining = rate_limit.core.remaining
                reset_time = rate_limit.core.reset
            except AttributeError:
                # Fallback for different PyGithub versions
                remaining = rate_limit.rate.remaining
                reset_time = rate_limit.rate.reset
            
            if remaining < 100 and not self.rate_limit_warned:
                self.log(f"⚠️  Warning: Only {remaining} API calls remaining")
                self.log(f"   Rate limit resets at: {reset_time}")
                self.rate_limit_warned = True
            
            if remaining < 10:
                wait_time = (reset_time - datetime.now()).total_seconds()
                if wait_time > 0:
                    self.log(f"⏳ Rate limit nearly exhausted. Waiting {wait_time:.0f}s...")
                    time.sleep(wait_time + 5)
        except Exception as e:
            # If rate limit check fails, just continue
            if not self.rate_limit_warned:
                self.log(f"⚠️  Warning: Could not check rate limit: {e}")
                self.rate_limit_warned = True
    
    def collect_issues(
        self,
        repo_name: str,
        labels: List[str] = None,
        max_issues: int = 50,
        state: str = 'closed',
        since: Optional[datetime] = None,
        language_filter: Optional[str] = None,
        extract_imports: bool = False
    ) -> List[IssueData]:
        """
        Collect issues with their fixes
        
        Args:
            repo_name: Repository name (e.g., 'owner/repo')
            labels: Filter by labels (e.g., ['bug', 'good first issue'])
            max_issues: Maximum issues to collect
            state: Issue state ('open', 'closed', 'all')
            since: Only issues updated after this date
            language_filter: Filter files by language (e.g., 'python', 'javascript')
            extract_imports: Extract import statements for relationship analysis
        
        Returns:
            List of structured issue data
        """
        self.log(f"📦 Collecting issues from {repo_name}...")
        
        try:
            repo = self.gh.get_repo(repo_name)
        except GithubException as e:
            self.log(f"❌ Error accessing repository: {e}")
            return []
        
        # Get repository language
        repo_language = self._detect_language(repo)
        if language_filter and repo_language.lower() != language_filter.lower():
            self.log(f"⚠️  Repository language ({repo_language}) doesn't match filter ({language_filter})")
        
        # Get test framework
        test_framework = self._detect_test_framework(repo, repo_language)
        
        self.log(f"   Language: {repo_language}")
        self.log(f"   Test Framework: {test_framework}")
        
        # Fetch issues
        # Build kwargs for get_issues (only include since if provided)
        issue_kwargs = {
            'state': state,
            'labels': labels if labels else []
        }
        if since:
            issue_kwargs['since'] = since
        
        issues = repo.get_issues(**issue_kwargs)
        
        dataset = []
        processed = 0
        skipped = 0
        
        for issue in issues:
            if processed >= max_issues:
                break
            
            self.check_rate_limit()
            
            # Skip pull requests (GitHub API returns both)
            if issue.pull_request:
                continue
            
            self.log(f"\n🔍 Processing issue #{issue.number}: {issue.title[:60]}...")
            
            # Find fixing PR
            fix_pr = self._find_fixing_pr(repo, issue)
            
            if not fix_pr:
                self.log(f"   ⏭️  Skipped: No fixing PR found")
                skipped += 1
                continue
            
            self.log(f"   ✅ Found fixing PR #{fix_pr.number}")
            
            # Extract code changes
            code_data = self._extract_code_changes(
                repo, 
                fix_pr, 
                repo_language,
                language_filter,
                issue=issue,
                extract_imports=extract_imports
            )
            
            if not code_data['buggy_files']:
                self.log(f"   ⏭️  Skipped: No relevant code files found")
                skipped += 1
                continue
            
            # Build issue data
            issue_data = IssueData(
                id=f"{repo_name}/issue_{issue.number}",
                metadata={
                    "repo": repo_name,
                    "issue_number": issue.number,
                    "issue_url": issue.html_url,
                    "pr_number": fix_pr.number,
                    "pr_url": fix_pr.html_url,
                    "language": repo_language,
                    "test_framework": test_framework,
                    "created_at": issue.created_at.isoformat(),
                    "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
                },
                issue={
                    "title": issue.title,
                    "description": issue.body or "",
                    "labels": [label.name for label in issue.labels],
                    "author": issue.user.login,
                    "comments_count": issue.comments,
                },
                code_context={
                    "buggy_files": code_data['buggy_files'],
                    "related_files": code_data['related_files'],
                },
                fix={
                    "pr_number": fix_pr.number,
                    "pr_title": fix_pr.title,
                    "pr_description": fix_pr.body or "",
                    "commits_count": fix_pr.commits,
                    "files_changed": fix_pr.changed_files,
                    "fixed_files": code_data['fixed_files'],
                },
                expected_test={
                    "path": self._generate_test_path(
                        code_data['buggy_files'][0]['path'],
                        test_framework,
                        repo_language
                    ),
                    "content": ""  # To be generated
                }
            )
            
            dataset.append(issue_data)
            processed += 1
            
            self.log(f"   ✅ Added to dataset ({processed}/{max_issues})")
        
        self.log(f"\n{'='*70}")
        self.log(f"Collection complete!")
        self.log(f"   Processed: {processed}")
        self.log(f"   Skipped: {skipped}")
        self.log(f"   Total: {processed + skipped}")
        self.log(f"{'='*70}")
        
        return dataset
    
    def _find_fixing_pr(self, repo: Repository, issue: Issue) -> Optional[PullRequest]:
        """Find PR that fixes this issue"""
        
        # Strategy 1: Look in issue timeline for linked PRs
        try:
            timeline = issue.get_timeline()
            for event in timeline:
                if event.event == "cross-referenced":
                    if hasattr(event, 'source') and event.source:
                        if hasattr(event.source, 'issue'):
                            pr_issue = event.source.issue
                            if pr_issue.pull_request:
                                pr = repo.get_pull(pr_issue.number)
                                if pr.state == 'closed' and pr.merged:
                                    return pr
        except Exception as e:
            self.log(f"   Warning: Timeline check failed: {e}")
        
        # Strategy 2: Search for keywords in PR body/title
        keywords = [
            f"fix #{issue.number}",
            f"fixes #{issue.number}",
            f"fixed #{issue.number}",
            f"close #{issue.number}",
            f"closes #{issue.number}",
            f"closed #{issue.number}",
            f"resolve #{issue.number}",
            f"resolves #{issue.number}",
            f"resolved #{issue.number}",
        ]
        
        try:
            # Search recent closed PRs
            prs = repo.get_pulls(state='closed', sort='updated', direction='desc')
            
            for pr in list(prs)[:100]:  # Check last 100 PRs
                if not pr.merged:
                    continue
                
                # Check PR body
                pr_text = (pr.title + " " + (pr.body or "")).lower()
                
                for keyword in keywords:
                    if keyword.lower() in pr_text:
                        # Verify PR was merged after issue was created
                        if pr.merged_at and pr.merged_at > issue.created_at:
                            return pr
        except Exception as e:
            self.log(f"   Warning: PR search failed: {e}")
        
        return None
    
    def _extract_code_changes(
        self, 
        repo: Repository, 
        pr: PullRequest,
        repo_language: str,
        language_filter: Optional[str] = None,
        issue: Optional[Issue] = None,
        extract_imports: bool = False
    ) -> Dict:
        """Extract code changes from PR
        
        Args:
            repo: GitHub repository
            pr: Pull request with the fix
            repo_language: Repository primary language
            language_filter: Optional language filter
            issue: Issue object (for identifying primary buggy file)
            extract_imports: If True, extract import statements from files
        """
        
        # File extensions to consider
        extensions = self._get_language_extensions(repo_language)
        
        if language_filter:
            extensions = self._get_language_extensions(language_filter)
        
        # Collect all files first
        all_source_files = []
        test_files = []
        
        try:
            files = pr.get_files()
            
            for file in files:
                # Check if file matches language
                if not any(file.filename.endswith(ext) for ext in extensions):
                    continue
                
                # Skip deleted files
                if file.status == 'removed':
                    continue
                
                # Separate test files from source files
                if self._is_test_file(file.filename):
                    test_files.append(file)
                else:
                    all_source_files.append(file)
        
        except Exception as e:
            self.log(f"   Error getting PR files: {e}")
            return {
                "buggy_files": [],
                "fixed_files": [],
                "related_files": []
            }
        
        if not all_source_files:
            return {
                "buggy_files": [],
                "fixed_files": [],
                "related_files": []
            }
        
        # Identify primary buggy file
        primary_file = self._identify_primary_buggy_file(
            all_source_files, 
            issue
        )
        
        # Collect file contents
        buggy_files = []
        fixed_files = []
        related_files = []
        
        for file in all_source_files:
            is_primary = (file.filename == primary_file)
            
            try:
                # Get content before fix
                before_content = None
                if file.status != 'added':
                    before_content = repo.get_contents(
                        file.filename,
                        ref=pr.base.sha
                    ).decoded_content.decode('utf-8', errors='ignore')
                
                # Get content after fix
                after_content = repo.get_contents(
                    file.filename,
                    ref=pr.head.sha
                ).decoded_content.decode('utf-8', errors='ignore')
                
                # Build file data
                file_data = {
                    "path": file.filename,
                    "content": before_content or after_content,
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "changes": file.changes,
                    "status": file.status,
                    "patch": file.patch if hasattr(file, 'patch') else None
                }
                
                # Extract imports if requested
                if extract_imports:
                    file_data["imports"] = self._extract_imports(
                        before_content or after_content,
                        repo_language
                    )
                
                # Add to appropriate list
                if is_primary:
                    file_data["is_primary"] = True
                    buggy_files.append(file_data)
                else:
                    file_data["type"] = "source"
                    file_data["relationship"] = "co_changed"
                    related_files.append(file_data)
                
                # Add fixed version
                fixed_files.append({
                    "path": file.filename,
                    "content": after_content,
                    "status": file.status
                })
                
            except Exception as e:
                self.log(f"   Warning: Could not get content for {file.filename}: {e}")
                continue
        
        # Add test files to related_files
        for test_file in test_files:
            try:
                test_content = repo.get_contents(
                    test_file.filename,
                    ref=pr.head.sha
                ).decoded_content.decode('utf-8', errors='ignore')
                
                related_files.append({
                    "path": test_file.filename,
                    "type": "test",
                    "status": test_file.status,
                    "changes": test_file.changes,
                    "relationship": "existing_test"
                })
            except Exception as e:
                self.log(f"   Warning: Could not get test file {test_file.filename}: {e}")
        
        # Calculate relationships if imports were extracted
        if extract_imports and buggy_files:
            self._calculate_relationships(buggy_files[0], related_files)
        
        return {
            "buggy_files": buggy_files,
            "fixed_files": fixed_files,
            "related_files": related_files
        }
    
    def _identify_primary_buggy_file(
        self, 
        source_files: List, 
        issue: Optional[Issue] = None
    ) -> str:
        """Identify which file is the primary buggy file
        
        Strategy:
        1. Check if issue mentions a specific file
        2. Otherwise, use file with most changes
        """
        
        if not source_files:
            return None
        
        # Strategy 1: Check if issue mentions a file
        if issue:
            issue_text = (issue.title + " " + (issue.body or "")).lower()
            
            for file in source_files:
                # Check for full path or just filename
                filename = file.filename.split('/')[-1]
                
                if file.filename.lower() in issue_text:
                    self.log(f"   Primary file (mentioned in issue): {file.filename}")
                    return file.filename
                
                if filename.lower() in issue_text:
                    self.log(f"   Primary file (mentioned in issue): {file.filename}")
                    return file.filename
        
        # Strategy 2: File with most changes
        primary = max(source_files, key=lambda f: f.changes)
        self.log(f"   Primary file (most changes: {primary.changes}): {primary.filename}")
        return primary.filename
    
    def _extract_imports(self, content: str, language: str) -> List[str]:
        """Extract import statements from source code
        
        This is optional - only used if extract_imports=True
        """
        
        if not content:
            return []
        
        imports = []
        
        try:
            if language == 'python':
                # Match: import X, from Y import Z
                import_matches = re.findall(
                    r'^\s*(?:from\s+([a-zA-Z0-9_.]+)|import\s+([a-zA-Z0-9_.]+))',
                    content,
                    re.MULTILINE
                )
                for match in import_matches:
                    module = match[0] or match[1]
                    if module:
                        imports.append(module.split('.')[0])
            
            elif language in ['javascript', 'typescript']:
                # Match: import X from 'Y', require('Z')
                import_matches = re.findall(
                    r'(?:import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]|require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\))',
                    content
                )
                for match in import_matches:
                    module = match[0] or match[1]
                    if module and not module.startswith('.'):
                        imports.append(module.split('/')[0])
            
            elif language == 'java':
                # Match: import X.Y.Z
                import_matches = re.findall(
                    r'^\s*import\s+([a-zA-Z0-9_.]+);',
                    content,
                    re.MULTILINE
                )
                imports = [imp.split('.')[0] for imp in import_matches]
            
            elif language == 'go':
                # Match: import "X" or import ( "X" "Y" )
                import_matches = re.findall(
                    r'import\s+(?:\(\s*)?["\']([^"\']+)["\']',
                    content
                )
                imports = [imp.split('/')[0] for imp in import_matches]
        
        except Exception as e:
            # If import extraction fails, just return empty list
            pass
        
        # Remove duplicates
        return list(set(imports))
    
    def _calculate_relationships(
        self, 
        primary_file: Dict, 
        related_files: List[Dict]
    ):
        """Calculate relationships between primary file and related files
        
        This modifies related_files in place to add relationship info
        Only used if extract_imports=True
        """
        
        if not primary_file.get('imports'):
            return
        
        primary_imports = set(primary_file['imports'])
        primary_name = primary_file['path'].split('/')[-1].rsplit('.', 1)[0]
        
        for related_file in related_files:
            if related_file.get('type') == 'test':
                continue
            
            # Get related file name and imports
            related_name = related_file['path'].split('/')[-1].rsplit('.', 1)[0]
            related_imports = set(related_file.get('imports', []))
            
            # Check if primary imports this file
            if related_name in primary_imports:
                related_file['relationship'] = 'imported_by_primary'
                continue
            
            # Check if this file imports primary
            if primary_name in related_imports:
                related_file['relationship'] = 'imports_primary'
                continue
            
            # Check for shared imports
            shared = primary_imports & related_imports
            if shared:
                related_file['relationship'] = 'shared_imports'
                related_file['shared_imports'] = list(shared)
                continue
    
    def _detect_language(self, repo: Repository) -> str:
        """Detect primary repository language"""
        try:
            langs = repo.get_languages()
            if langs:
                return max(langs, key=langs.get).lower()
        except Exception as e:
            self.log(f"   Warning: Could not detect language: {e}")
        
        return "unknown"
    
    def _detect_test_framework(self, repo: Repository, language: str) -> str:
        """Detect test framework used in repository"""
        
        framework_map = {
            "python": [
                ("pytest.ini", "pytest"),
                ("setup.cfg", "pytest"),
                ("tox.ini", "pytest"),
                (".pytest_cache", "pytest"),
                ("unittest", "unittest"),
            ],
            "javascript": [
                ("jest.config.js", "jest"),
                ("jest.config.ts", "jest"),
                (".jestrc", "jest"),
                ("mocha.opts", "mocha"),
                ("karma.conf.js", "karma"),
            ],
            "typescript": [
                ("jest.config.ts", "jest"),
                ("jest.config.js", "jest"),
            ],
            "java": [
                ("pom.xml", "junit"),
                ("build.gradle", "junit"),
            ],
            "go": [
                ("*_test.go", "testing"),
            ],
            "ruby": [
                ("spec", "rspec"),
                (".rspec", "rspec"),
            ],
        }
        
        try:
            # Check root directory
            contents = repo.get_contents("")
            
            if language in framework_map:
                for filename, framework in framework_map[language]:
                    for content in contents:
                        if content.name == filename or content.name.endswith(filename):
                            return framework
        except Exception as e:
            self.log(f"   Warning: Could not detect test framework: {e}")
        
        # Default frameworks by language
        defaults = {
            "python": "pytest",
            "javascript": "jest",
            "typescript": "jest",
            "java": "junit",
            "go": "testing",
            "ruby": "rspec",
        }
        
        return defaults.get(language, "unknown")
    
    def _get_language_extensions(self, language: str) -> List[str]:
        """Get file extensions for language"""
        
        extension_map = {
            "python": [".py"],
            "javascript": [".js", ".jsx"],
            "typescript": [".ts", ".tsx"],
            "java": [".java"],
            "go": [".go"],
            "ruby": [".rb"],
            "php": [".php"],
            "c": [".c", ".h"],
            "c++": [".cpp", ".cc", ".cxx", ".hpp", ".h"],
            "csharp": [".cs"],
            "swift": [".swift"],
            "kotlin": [".kt"],
            "rust": [".rs"],
        }
        
        return extension_map.get(language.lower(), [])
    
    def _is_test_file(self, filename: str) -> bool:
        """Check if file is a test file"""
        
        test_patterns = [
            r"test_.*\.py$",
            r".*_test\.py$",
            r".*\.test\.(js|ts|jsx|tsx)$",
            r".*\.spec\.(js|ts|jsx|tsx)$",
            r".*Test\.java$",
            r".*_test\.go$",
            r".*_spec\.rb$",
        ]
        
        for pattern in test_patterns:
            if re.search(pattern, filename):
                return True
        
        test_dirs = ["test", "tests", "__tests__", "spec", "specs"]
        
        for test_dir in test_dirs:
            if f"/{test_dir}/" in filename or filename.startswith(f"{test_dir}/"):
                return True
        
        return False
    
    def _generate_test_path(self, source_path: str, framework: str, language: str) -> str:
        """Generate test file path for source file"""
        
        # Extract filename
        parts = source_path.split('/')
        filename = parts[-1]
        name_without_ext = filename.rsplit('.', 1)[0]
        
        # Generate test filename based on language/framework
        if language == "python":
            test_filename = f"test_{name_without_ext}.py"
            test_dir = "tests"
        elif language in ["javascript", "typescript"]:
            ext = "ts" if language == "typescript" else "js"
            test_filename = f"{name_without_ext}.test.{ext}"
            test_dir = "__tests__"
        elif language == "java":
            test_filename = f"{name_without_ext}Test.java"
            test_dir = "src/test/java"
        elif language == "go":
            test_filename = f"{name_without_ext}_test.go"
            test_dir = "."  # Go tests in same directory
        else:
            test_filename = f"test_{filename}"
            test_dir = "tests"
        
        if test_dir == ".":
            return f"{'/'.join(parts[:-1])}/{test_filename}" if len(parts) > 1 else test_filename
        else:
            return f"{test_dir}/{test_filename}"
    
    def save_dataset(self, dataset: List[IssueData], output_file: str):
        """Save dataset to JSON file"""
        
        self.log(f"\n💾 Saving dataset to {output_file}...")
        
        # Convert dataclasses to dicts
        dataset_dict = {
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_issues": len(dataset),
                "collector_version": "1.0.0"
            },
            "dataset": [asdict(item) for item in dataset]
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(dataset_dict, f, indent=2, ensure_ascii=False)
        
        self.log(f"✅ Saved {len(dataset)} issues to {output_file}")
    
    def print_statistics(self, dataset: List[IssueData]):
        """Print dataset statistics"""
        
        if not dataset:
            self.log("\nNo data to show statistics for.")
            return
        
        languages = {}
        frameworks = {}
        labels = {}
        
        for item in dataset:
            # Count languages
            lang = item.metadata['language']
            languages[lang] = languages.get(lang, 0) + 1
            
            # Count frameworks
            fw = item.metadata['test_framework']
            frameworks[fw] = frameworks.get(fw, 0) + 1
            
            # Count labels
            for label in item.issue['labels']:
                labels[label] = labels.get(label, 0) + 1
        
        print(f"\n{'='*70}")
        print("DATASET STATISTICS")
        print(f"{'='*70}")
        print(f"Total issues: {len(dataset)}")
        print(f"\nLanguages:")
        for lang, count in sorted(languages.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {lang}: {count}")
        print(f"\nTest Frameworks:")
        for fw, count in sorted(frameworks.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {fw}: {count}")
        print(f"\nTop Labels:")
        for label, count in sorted(labels.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  - {label}: {count}")
        print(f"{'='*70}")


def main():
    """Example usage"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Collect GitHub issues with fixes for test generation"
    )
    parser.add_argument(
        "repo",
        help="Repository name (e.g., 'owner/repo')"
    )
    parser.add_argument(
        "--token",
        help="GitHub token (or set GITHUB_TOKEN env var)",
        default=os.getenv("GITHUB_TOKEN")
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        help="Filter by labels (e.g., bug 'good first issue')",
        default=["bug"]
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=50,
        help="Maximum issues to collect (default: 50)"
    )
    parser.add_argument(
        "--language",
        help="Filter by language (e.g., python, javascript)",
        default=None
    )
    parser.add_argument(
        "--output",
        help="Output file (default: github_issues_REPO.json)",
        default=None
    )
    parser.add_argument(
        "--state",
        choices=["open", "closed", "all"],
        default="closed",
        help="Issue state (default: closed)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress messages"
    )
    parser.add_argument(
        "--extract-imports",
        action="store_true",
        help="Extract import statements for smart relationship analysis (slower)"
    )
    
    args = parser.parse_args()
    
    if not args.token:
        print("❌ Error: GitHub token required")
        print("Set GITHUB_TOKEN environment variable or use --token")
        return 1
    
    # Generate output filename if not specified
    if not args.output:
        repo_name = args.repo.replace('/', '_')
        args.output = f"github_issues_{repo_name}.json"
    
    # Create collector
    collector = GitHubIssueCollector(
        github_token=args.token,
        verbose=not args.quiet
    )
    
    # Collect issues
    dataset = collector.collect_issues(
        repo_name=args.repo,
        labels=args.labels,
        max_issues=args.max_issues,
        state=args.state,
        language_filter=args.language,
        extract_imports=args.extract_imports
    )
    
    if not dataset:
        print("⚠️  No issues collected")
        return 0
    
    # Save dataset
    collector.save_dataset(dataset, args.output)
    
    # Print statistics
    collector.print_statistics(dataset)
    
    return 0


if __name__ == "__main__":
    exit(main())
