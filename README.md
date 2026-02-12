# GitHub Issue Collector for Test Generation

Collect closed bug issues with their fixes from GitHub repositories for LLM-based test generation research.

## 🎯 Features

- ✅ **Collect issues with fixes** - Automatically finds PRs that fixed issues
- ✅ **Extract code changes** - Gets before/after code from commits
- ✅ **Multi-language support** - Python, JavaScript, Java, Go, and more
- ✅ **Batch collection** - Process multiple repos at once
- ✅ **Smart filtering** - Filter by language, labels, complexity, etc.
- ✅ **Dataset analysis** - Built-in statistics and insights
- ✅ **Rate limit handling** - Automatic rate limit management

## 📦 Installation

```bash
# Clone or download this directory
cd github-collector

# Install dependencies
pip install -r requirements.txt

# Set your GitHub token
export GITHUB_TOKEN='your-github-personal-access-token'
```

### Getting a GitHub Token

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes: `repo` (Full control of private repositories)
4. Generate and copy the token
5. Set it: `export GITHUB_TOKEN='your-token'`

## 🚀 Quick Start

### Collect from a Single Repository

```bash
# Basic usage - collect 50 bug issues from a repo
python collect_issues.py owner/repo

# With options
python collect_issues.py httpie/cli \
  --labels bug "good first issue" \
  --max-issues 30 \
  --language python \
  --output httpie_bugs.json
```

**NEW: Smart Related Code Collection** 🎯

The collector now intelligently identifies the primary buggy file and collects related source files for better test generation context:

```bash
# Default: Quick fix mode (fast, good quality)
python collect_issues.py httpie/cli --max-issues 30

# Smart mode: Extract imports for relationship analysis (best quality)
python collect_issues.py httpie/cli --max-issues 30 --extract-imports
```

See [RELATED_CODE_GUIDE.md](RELATED_CODE_GUIDE.md) for details on the improved data structure.

### Collect from Multiple Repositories

```bash
# Using a config file
python batch_collect.py --config repos_config.yaml

# Using command line
python batch_collect.py \
  --repos psf/requests pallets/flask django/django \
  --labels bug \
  --max-issues 20 \
  --output python_bugs.json
```

### Filter and Analyze Dataset

```bash
# Show statistics
python filter_dataset.py dataset.json --stats-only

# Filter to English only
python filter_dataset.py dataset.json \
  --english-only \
  --output english_only.json

# Filter by language
python filter_dataset.py dataset.json \
  --language python \
  --output python_only.json

# Filter by complexity
python filter_dataset.py dataset.json \
  --max-files 3 \
  --max-changes 50 \
  --exclude-tests \
  --output simple_bugs.json

# Take a random sample
python filter_dataset.py dataset.json \
  --sample 20 \
  --sort-by complexity \
  --output sample_20.json
```

## 📚 Detailed Usage

### Single Repository Collection

```bash
python collect_issues.py REPO [OPTIONS]

Required:
  REPO                  Repository name (e.g., 'owner/repo')

Options:
  --token TOKEN         GitHub token (or set GITHUB_TOKEN env var)
  --labels LABEL [...]  Filter by labels (default: bug)
  --max-issues N        Maximum issues to collect (default: 50)
  --language LANG       Filter by language (python, javascript, etc.)
  --state STATE         Issue state: open, closed, all (default: closed)
  --output FILE         Output file (default: github_issues_REPO.json)
  --quiet               Suppress progress messages
```

**Examples:**

```bash
# Collect 100 Python bugs from requests
python collect_issues.py psf/requests \
  --max-issues 100 \
  --language python

# Collect open issues with specific labels
python collect_issues.py pallets/flask \
  --labels bug "needs triage" \
  --state open \
  --max-issues 20

# Quiet mode, custom output
python collect_issues.py django/django \
  --quiet \
  --output django_bugs.json
```

### Batch Collection

```bash
python batch_collect.py [OPTIONS]

Options:
  --config FILE         YAML/JSON config file with repo list
  --repos REPO [...]    Repository names
  --token TOKEN         GitHub token
  --labels LABEL [...]  Labels to filter (default: bug)
  --max-issues N        Max issues per repo (default: 30)
  --output FILE         Merged dataset output (default: merged_dataset.json)
  --no-merge            Don't merge, only create individual files
```

**Config File Format (YAML):**

```yaml
repositories:
  - name: owner/repo1
    labels: [bug, enhancement]
    max_issues: 30
    language: python

  - name: owner/repo2
    labels: [bug]
    max_issues: 20
    language: javascript

defaults:
  max_issues: 20
  state: closed
```

**Config File Format (JSON):**

```json
{
  "repositories": [
    {
      "name": "owner/repo1",
      "labels": ["bug"],
      "max_issues": 30,
      "language": "python"
    }
  ]
}
```

**Examples:**

```bash
# Use config file
python batch_collect.py --config repos_config.yaml

# Command line repos
python batch_collect.py \
  --repos httpie/cli requests/requests \
  --max-issues 25

# Don't merge into single file
python batch_collect.py \
  --config repos.yaml \
  --no-merge
```

### Dataset Filtering

```bash
python filter_dataset.py INPUT [OPTIONS]

Required:
  INPUT                 Input dataset JSON file

Options:
  --output FILE         Output file for filtered dataset
  --language LANG       Filter by language
  --label LABEL         Filter by label
  --repo PATTERN        Filter by repository (supports wildcards)
  --max-files N         Maximum changed files
  --max-lines N         Maximum total lines
  --max-changes N       Maximum line changes
  --sample N            Random sample of N issues
  --exclude-tests       Exclude test-only issues
  --english-only        Keep only English-language issues
  --sort-by TYPE        Sort by: complexity, date
  --stats-only          Only show statistics
  --export-simple FILE  Export simplified version
```

**Examples:**

```bash
# Simple Python bugs only
python filter_dataset.py merged_dataset.json \
  --language python \
  --max-files 2 \
  --max-changes 30 \
  --output simple_python.json

# Sample for manual review
python filter_dataset.py large_dataset.json \
  --sample 50 \
  --sort-by complexity \
  --export-simple review_sample.json

# Statistics only
python filter_dataset.py dataset.json --stats-only

# Complex filtering
python filter_dataset.py dataset.json \
  --repo "django/*" \
  --label "confirmed bug" \
  --exclude-tests \
  --max-lines 200 \
  --output django_simple.json
```

## 📊 Output Dataset Format

```json
{
  "metadata": {
    "created_at": "2024-02-07T10:30:00",
    "total_issues": 50
  },
  "dataset": [
    {
      "id": "owner/repo/issue_123",
      "metadata": {
        "repo": "owner/repo",
        "issue_number": 123,
        "issue_url": "https://github.com/...",
        "pr_number": 125,
        "pr_url": "https://github.com/...",
        "language": "python",
        "test_framework": "pytest",
        "created_at": "2024-01-15T...",
        "closed_at": "2024-01-20T..."
      },
      "issue": {
        "title": "Bug: Division by zero",
        "description": "Full issue description...",
        "labels": ["bug", "good first issue"],
        "author": "username",
        "comments_count": 5
      },
      "code_context": {
        "buggy_files": [
          {
            "path": "src/math_utils.py",
            "content": "def divide(a, b):\n    return a / b",
            "additions": 2,
            "deletions": 1,
            "changes": 3,
            "status": "modified",
            "patch": "@@ -10,1 +10,2 @@..."
          }
        ],
        "related_files": [
          {
            "path": "tests/test_math.py",
            "type": "test",
            "status": "modified"
          }
        ]
      },
      "fix": {
        "pr_number": 125,
        "pr_title": "Fix division by zero",
        "pr_description": "PR description...",
        "commits_count": 1,
        "files_changed": 2,
        "fixed_files": [
          {
            "path": "src/math_utils.py",
            "content": "def divide(a, b):\n    if b == 0:\n        raise ValueError(...)\n    return a / b",
            "status": "modified"
          }
        ]
      },
      "expected_test": {
        "path": "tests/test_math_utils.py",
        "content": ""
      }
    }
  ]
}
```

## 🔧 Advanced Usage

### Custom Filtering Logic

```python
from filter_dataset import DatasetFilter

# Load dataset
ds = DatasetFilter("dataset.json")

# Chain multiple filters
filtered = (ds
    .filter_by_language("python")
    .filter_by_file_count(max_files=3)
    .exclude_test_files()
    .sample(30)
    .sort_by_complexity()
)

# Get statistics
stats = filtered.statistics()
print(f"Average complexity: {stats['avg_lines_changed']}")

# Save
filtered.save("filtered_output.json")
```

### Programmatic Collection

```python
from collect_issues import GitHubIssueCollector

# Create collector
collector = GitHubIssueCollector(
    github_token="your-token",
    verbose=True
)

# Collect issues
dataset = collector.collect_issues(
    repo_name="owner/repo",
    labels=["bug", "enhancement"],
    max_issues=50,
    language_filter="python"
)

# Process issues
for issue_data in dataset:
    print(f"Issue #{issue_data.metadata['issue_number']}")
    print(f"Files changed: {len(issue_data.code_context['buggy_files'])}")

# Save
collector.save_dataset(dataset, "my_dataset.json")
```

### Batch Collection with Custom Logic

```python
from batch_collect import BatchCollector

batch = BatchCollector("your-token")

# Collect from multiple repos
results = batch.collect_from_list(
    repo_names=["owner/repo1", "owner/repo2"],
    labels=["bug"],
    max_issues_per_repo=30
)

# Merge all results
batch.merge_datasets(results, "merged.json")
```

## 📈 Example Workflows

### Research Dataset Creation

```bash
# 1. Collect from multiple Python projects
python batch_collect.py --config python_repos.yaml

# 2. Filter to simple bugs (for initial experiments)
python filter_dataset.py merged_dataset.json \
  --max-files 2 \
  --max-changes 30 \
  --exclude-tests \
  --output simple_bugs.json

# 3. Create test/validation split
python filter_dataset.py simple_bugs.json \
  --sample 40 \
  --output train_set.json

python filter_dataset.py simple_bugs.json \
  --sample 10 \
  --output test_set.json
```

### Language-Specific Collection

```bash
# Python bugs
python batch_collect.py \
  --repos psf/requests pallets/flask django/django \
  --language python \
  --max-issues 30 \
  --output python_bugs.json

# JavaScript bugs
python batch_collect.py \
  --repos axios/axios lodash/lodash \
  --language javascript \
  --max-issues 30 \
  --output js_bugs.json
```

### Quality Control

```bash
# Get simple, well-documented bugs
python filter_dataset.py raw_dataset.json \
  --max-files 3 \
  --max-changes 50 \
  --exclude-tests \
  --sort-by complexity \
  --sample 50 \
  --export-simple review_list.json

# Manually review review_list.json
# Then extract the good ones
python filter_dataset.py raw_dataset.json \
  --repo "owner/specific-repo" \
  --output curated_dataset.json
```

## ⚡ Tips & Best Practices

### Rate Limits

GitHub API has rate limits:

- **Authenticated**: 5,000 requests/hour
- **Unauthenticated**: 60 requests/hour

The collector automatically monitors and warns about rate limits. If you hit the limit, it will wait until reset.

### Choosing Repositories

Good repositories for test generation research:

- **Active projects** with regular bug fixes
- **Well-labeled issues** (bug, enhancement, etc.)
- **Clear issue descriptions** with reproduction steps
- **Small to medium size** (easier to understand context)

Examples:

- Python: `requests`, `flask`, `click`, `httpie/cli`
- JavaScript: `axios`, `lodash`, `express`
- Java: `spring-boot`, `junit5`

### Data Quality

For best results:

1. **Filter by complexity** - Start with simple bugs (1-3 files, <50 changes)
2. **Exclude test-only changes** - Focus on source code bugs
3. **Check descriptions** - Issues with clear reproduction steps work best
4. **Verify fixes** - Make sure the PR actually fixes the issue

### Storage

Datasets can get large:

- **50 issues**: ~5-10 MB
- **500 issues**: ~50-100 MB
- **5000 issues**: ~500 MB - 1 GB

Consider:

- Filtering before saving
- Using `--export-simple` for overview
- Compressing large datasets

## 🐛 Troubleshooting

**Rate limit errors:**

```bash
# Check your rate limit
curl -H "Authorization: token YOUR_TOKEN" \
  https://api.github.com/rate_limit

# Wait or use a different token
```

**No PRs found:**

- Repository may not link issues to PRs
- Try different label combinations
- Check if issues are actually closed with fixes

**Import errors:**

```bash
pip install -r requirements.txt
```

**Token authentication failed:**

- Verify token is valid: https://github.com/settings/tokens
- Check token has `repo` scope
- Make sure it's exported: `echo $GITHUB_TOKEN`

## 📝 File Descriptions

- `collect_issues.py` - Main collector for single repositories
- `batch_collect.py` - Batch collector for multiple repos
- `filter_dataset.py` - Filter and analyze datasets
- `repos_config.yaml` - Example YAML configuration
- `repos_config.json` - Example JSON configuration
- `requirements.txt` - Python dependencies

## 🤝 Contributing

Ideas for improvements:

- Add more language support
- Better PR detection algorithms
- Integration with test generation tools
- Dataset quality metrics
- Automatic test suite detection

## 📄 License

This is a research tool for educational purposes.

## 🧪 Test Generation Workflow

After collecting and filtering your dataset, generate unit tests with LLM and validate against real code!

### Three Testing Modes

#### 1. Local Repository Validation (Recommended for Thesis) ⭐

**Best for:** Final validation, thesis results, high-quality benchmarking

```bash
# Generate tests and validate against REAL GitHub commits
python generate_tests_local.py clean_dataset.json

# Test with just 1 issue first
python generate_tests_local.py clean_dataset.json --max-issues 1
```

**What it does:**

1. Clones the repository locally
2. Generates test with LLM (Chain of Thought)
3. Checks out buggy commit → runs test (should FAIL ❌)
4. Checks out fixed commit → runs test (should PASS ✅)

**Prerequisites:**

- Git installed
- `pip install pytest` (for Python repos)
- Sufficient disk space for cloned repos

**See:** [LOCAL_TESTING_GUIDE.md](LOCAL_TESTING_GUIDE.md) for detailed guide

#### 2. Skip Execution Mode (Fast)

**Best for:** Quick iteration, prompt engineering, Windows compatibility

```bash
# Generate test + fix code without running
python generate_tests.py dataset.json --skip-execution
```

**What it does:**

- Generates test code with Chain of Thought
- Generates fix code with Chain of Thought
- Saves everything to JSON (no execution)

**Prerequisites:**

- Just `pip install anthropic`

#### 3. Sandbox Simulation Mode

**Best for:** Quick validation without cloning repos

```bash
# Generate test + fix, run in sandbox
python generate_tests.py dataset.json
```

**What it does:**

- Generates test + fix with LLM
- Runs both in temporary sandbox
- Good for quick checks

**See:** [MODE_COMPARISON.md](MODE_COMPARISON.md) to choose the right mode

### Complete Research Pipeline

```bash
# 1. Collect issues from repository
python collect_issues.py httpie/cli --max-issues 30

# 2. Filter to quality dataset
python filter_dataset.py github_issues_httpie_cli.json \
  --english-only \
  --max-changes 20 \
  --output research_dataset.json

# 3. Validate JSON
python validate_json.py research_dataset.json

# 4. Generate and validate tests (local mode)
python generate_tests_local.py research_dataset.json

# 5. Analyze results
cat local_test_results/summary.json
```

### Results Structure

```
local_test_results/
├── summary.json                 # Overall statistics
├── issue_513_result.json       # Per-issue details
└── issue_1136_result.json

test_repos/                     # Cloned repositories
└── agentscope/                 # Each repo cloned here
```

### Example Success Output

```json
{
  "summary": {
    "total": 10,
    "successful": 8,
    "success_rate": 0.8,
    "tests_generated": 10,
    "buggy_failures": 8,
    "fixed_passes": 8
  }
}
```

**For detailed guides:**

- 📘 [LOCAL_TESTING_GUIDE.md](LOCAL_TESTING_GUIDE.md) - Complete local testing guide
- 📊 [MODE_COMPARISON.md](MODE_COMPARISON.md) - Choose the right mode
- ⚡ [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Quick command reference
- 🔧 [SETUP_GUIDE.md](SETUP_GUIDE.md) - Installation and setup
- 🐛 [TROUBLESHOOTING_JSON.md](TROUBLESHOOTING_JSON.md) - Fix JSON errors

## 🙋 FAQ

**Q: How long does collection take?**
A: ~2-5 seconds per issue. 50 issues = ~2-5 minutes.

**Q: Can I collect from private repos?**
A: Yes, if your token has access.

**Q: What if issues don't have linked PRs?**
A: The collector tries multiple strategies to find fixing PRs, but some may be missed.

**Q: How do I get more issues per repo?**
A: Increase `--max-issues` (default is 50)

**Q: Can I pause and resume?**
A: No auto-resume yet. Recommend smaller batches.

**Q: What languages are supported?**
A: Python, JavaScript, TypeScript, Java, Go, Ruby, PHP, C, C++, C#, Swift, Kotlin, Rust

**Q: How do I report bugs?**
A: This is a demo tool - feel free to modify and improve it!
