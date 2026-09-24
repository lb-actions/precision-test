# precision-test

Coverage-based precision test selector for GitHub Actions. Analyzes code changes
and selects relevant test cases based on coverage data with line and function
granularity (file-level matching is reserved for renamed/deleted product code).

## Features

- **Multi-granularity matching**: Line-level and function-level test selection,
  with file-level matching applied internally to renamed/deleted product code
- **Coverage-based selection**: Uses coverage data to identify affected test cases
- **Multi-repo adapters**: Built-in adapters for `vllm_ascend`, `sglang`, and
  `torch_npu`, selected via the `repo` input
- **PR integration**: Fetches PR diff from GitHub API (`vllm_ascend` / `sglang`)
  or GitCode API (`torch_npu`)
- **Security hardening**: Path traversal validation and subprocess execution
  timeout

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
    repo: vllm_ascend
    github-pr: ${{ github.repository }}#${{ github.event.pull_request.number }}
    map-file: test_case_map.json
    build-map: 'true'
```

### GitCode PR (torch_npu)

```yaml
- name: Precision Test Selector
  uses: lb-actions/precision-test@v1.0.0
  with:
    repo: torch_npu
    gitcode-pr: Ascend/pytorch#${{ github.event.pull_request.number }}
    source-dir: covstub
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `repo` | Repository adapter: `vllm_ascend` / `sglang` / `torch_npu` | No | `vllm_ascend` |
| `github-pr` | GitHub PR (vllm_ascend / sglang), format: `owner/repo#pr_number` or just `pr_number` | No | - |
| `gitcode-pr` | GitCode PR (torch_npu), format: `owner/repo#pr_number`; mutually exclusive with `github-pr` | No | - |
| `source-dir` | Source code directory | No | `covstub` |
| `map-file` | Test case map file | No | `test_case_map.json` |
| `coverage-dir` | Coverage data directory (required only when building the map) | No | `coverage` |
| `build-map` | Rebuild test case mapping | No | `false` |
| `min-affected` | Minimum affected lines threshold | No | `1` |
| `dedup` | Enable deduplication | No | `false` |
| `enable-line-match` | Enable line-level matching | No | `true` |
| `enable-function-match` | Enable function-level matching | No | `true` |
| `skip-imports` | Skip import statement lines | No | `false` |

> File-level matching is reserved for renamed/deleted product code files and is
> not exposed as an input; it runs automatically when such changes are detected.

## Outputs

The action writes the recommended test list to `recommended_pytest_paths.txt`
in the workflow workspace and prints `test-list-file=<path>` and
`test-count=<n>` to the log. No outputs are declared in `action.yml`; read the
file directly in a subsequent step:

```yaml
- name: Run Selected Tests
  run: |
    if [ -f recommended_pytest_paths.txt ]; then
      pytest -n auto $(cat recommended_pytest_paths.txt)
    fi
```

## Environment Variables

The action forwards the workflow environment to the Python subprocess, so
token-based authentication is picked up automatically. Only the variables
below are read by the action (Node entry or Python package):

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` / `GH_TOKEN` | - | GitHub API token for PR diff fetch (optional; used by `vllm_ascend` / `sglang`). The default `github.token` is sufficient for public repos and same-org PRs. |
| `GITCODE_TOKEN` | - | GitCode API token (required for `torch_npu` when using `gitcode-pr`). Provide via a secret, e.g. `${{ secrets.GITCODE_TOKEN }}`. |
| `PYTHON_EXEC_TIMEOUT_SECONDS` | `600` | Overall Python subprocess execution timeout in seconds (DoS guard). |

### Example

```yaml
- name: Precision Test Selector
  uses: lb-actions/precision-test@v1.0.0
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    PYTHON_EXEC_TIMEOUT_SECONDS: 900
  with:
    github-pr: ${{ github.repository }}#${{ github.event.pull_request.number }}
```

For GitCode PRs, pass a `GITCODE_TOKEN` secret instead:

```yaml
- name: Precision Test Selector
  uses: lb-actions/precision-test@v1.0.0
  env:
    GITCODE_TOKEN: ${{ secrets.GITCODE_TOKEN }}
  with:
    repo: torch_npu
    gitcode-pr: Ascend/pytorch#${{ github.event.pull_request.number }}
```

## Coverage Data Format

The action expects coverage data in SQLite format with the following tables:

- `file`: File path information
- `arc`: Coverage arc data (fromno, tono)

## License

MIT
