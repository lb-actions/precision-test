# precision-test

Coverage-based precision test selector for GitHub Actions. Analyzes code changes
and selects relevant test cases based on coverage data with line, function, and
file granularity.

## Features

- **Multi-granularity matching**: Line-level, function-level, and file-level
  test selection
- **Coverage-based selection**: Uses coverage data to identify affected test cases
- **GitHub PR integration**: Automatically fetches PR diff from GitHub API
- **Security hardening**: Comprehensive DoS protection and input validation
- **Configurable**: All limits and thresholds configurable via environment variables

## Usage

### Basic Workflow

```yaml
name: Precision Test

on:
  pull_request:
    branches: [main]

jobs:
  select-tests:
    runs-on: ubuntu-latest
    outputs:
      test-count: ${{ steps.selector.outputs.test-count }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Precision Test Selector
        id: selector
        uses: lb-actions/precision-test@v1.0.0
        with:
          github-pr: ${{ github.repository }}#${{ github.event.pull_request.number }}
          source-dir: src
          coverage-dir: coverage
          min-affected: 1

      - name: Run Selected Tests
        run: |
          if [ -f recommended_pytest_paths.txt ]; then
            pytest -n auto $(cat recommended_pytest_paths.txt)
          fi
```

### With Test Case Map

```yaml
- name: Precision Test Selector
  uses: lb-actions/precision-test@v1.0.0
  with:
    github-pr: ${{ github.repository }}#${{ github.event.pull_request.number }}
    map-file: test_case_map.json
    build-map: 'true'
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `github-pr` | GitHub PR, format: `owner/repo#pr_number` or just `pr_number` | No | - |
| `source-dir` | Source code directory | No | `covstub` |
| `map-file` | Test case map file | No | `test_case_map.json` |
| `coverage-dir` | Coverage data directory | No | `coverage` |
| `build-map` | Rebuild test case mapping | No | `false` |
| `min-affected` | Minimum affected lines threshold | No | `1` |
| `dedup` | Enable deduplication | No | `false` |
| `enable-line-match` | Enable line-level matching | No | `true` |
| `enable-function-match` | Enable function-level matching | No | `true` |
| `enable-file-match` | Enable file-level matching | No | `true` |
| `skip-imports` | Skip import statement lines | No | `false` |
| `repo-name` | Repository name for path normalization | No | `vllm_ascend` |

## Outputs

| Output | Description |
|--------|-------------|
| `test-list-file` | Path to the file containing recommended test cases |
| `test-count` | Number of test cases recommended |

## Security Configuration

All security limits are configurable via environment variables:

### DoS Protection

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_DATABASE_SIZE_MB` | `100` | Maximum coverage database size in MB |
| `DATABASE_QUERY_TIMEOUT` | `60` | Database query timeout in seconds |
| `MAX_SOURCE_FILE_SIZE_MB` | `1` | Maximum source file size for parsing in MB |
| `MAX_PARSE_RECURSION_DEPTH` | `1000` | Maximum AST parsing recursion depth |
| `MAX_DIFF_SIZE_MB` | `50` | Maximum diff file size in MB |
| `REGEX_TIMEOUT_SECONDS` | `10` | Regex matching timeout in seconds |
| `PYTHON_EXEC_TIMEOUT_SECONDS` | `600` | Python subprocess execution timeout in seconds |

### Data Integrity

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_VALID_LINE_NUMBER` | `1000000` | Maximum valid line number |

### API Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | - | GitHub API token for higher rate limits (optional) |
| `REPO_NAME` | `vllm_ascend` | Repository name for path normalization |

### Example with Custom Limits

```yaml
- name: Precision Test Selector
  uses: lb-actions/precision-test@v1.0.0
  env:
    MAX_DATABASE_SIZE_MB: 200
    DATABASE_QUERY_TIMEOUT: 120
    PYTHON_EXEC_TIMEOUT_SECONDS: 900
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    github-pr: ${{ github.repository }}#${{ github.event.pull_request.number }}
```

## Coverage Data Format

The action expects coverage data in SQLite format with the following tables:

- `file`: File path information
- `arc`: Coverage arc data (fromno, tono)

## License

MIT