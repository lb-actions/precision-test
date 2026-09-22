/**
 * Precision Test Selector - GitHub Action Entry Point
 *
 * Coverage-based precision test selector supporting line, function, and file
 * granularity. Wraps the Python `test_selector` package (invoked via
 * `python -m test_selector`) and supports vllm_ascend / sglang (GitHub PR) and
 * torch_npu (GitCode PR).
 *
 * The bundled Python package ships next to this script at `dist/test_selector/`
 * and is made importable via PYTHONPATH so `python -m test_selector` resolves
 * it. All relative path inputs are resolved to absolute paths (against the
 * workflow workspace) before being forwarded, because the Python CLI resolves
 * relative paths against the package directory (BASE_DIR), not the workspace.
 */
import * as core from "@actions/core";
import * as exec from "@actions/exec";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/**
 * Security: Python subprocess execution timeout in seconds (prevents DoS).
 * Can be overridden via PYTHON_EXEC_TIMEOUT_SECONDS environment variable.
 */
const PYTHON_EXEC_TIMEOUT_SECONDS = parseInt(
  process.env.PYTHON_EXEC_TIMEOUT_SECONDS || "600",
  10,
);

/**
 * Execute a command with timeout. Wraps exec.exec() with timeout functionality.
 */
async function execWithTimeout(
  commandLine: string,
  args: string[],
  options: exec.ExecOptions,
  timeoutMs: number,
): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`Command timed out after ${timeoutMs / 1000} seconds`));
    }, timeoutMs);
    exec
      .exec(commandLine, args, options)
      .then((result) => {
        clearTimeout(timer);
        resolve(result);
      })
      .catch((error) => {
        clearTimeout(timer);
        reject(error);
      });
  });
}

/**
 * Validate a path for security: non-empty and free of traversal characters.
 */
function validatePath(inputPath: string, paramName: string): string {
  if (!inputPath || inputPath.trim() === "") {
    throw new Error(`${paramName} cannot be empty`);
  }
  if (path.normalize(inputPath).includes("..")) {
    throw new Error(`${paramName} contains path traversal characters: ${inputPath}`);
  }
  return path.resolve(inputPath);
}

interface SelectorParams {
  repo: string;
  githubPr: string;
  gitcodePr: string;
  sourceDir: string;
  mapFile: string;
  coverageDir: string;
  buildMap: boolean;
  minAffected: number;
  dedup: boolean;
  enableLineMatch: boolean;
  enableFunctionMatch: boolean;
  skipImports: boolean;
}

interface SelectorResult {
  success: boolean;
  testListFile: string;
  testCount: number;
}

/**
 * Execute precision test selector.
 */
async function runPrecisionTest(params: SelectorParams): Promise<SelectorResult> {
  const venvPath = path.join(os.tmpdir(), `precision_test_venv_${process.pid}`);
  // The bundled Python package lives next to this script at dist/test_selector/.
  const packageDir = path.resolve(__dirname, "test_selector");
  const distDir = path.resolve(__dirname);
  // Python writes output to BASE_DIR (the package's parent = dist).
  const outputFile = path.join(distDir, "recommended_pytest_paths.txt");
  // Mirror into the workspace so subsequent workflow steps can read it.
  const workspaceOutput = path.join(process.cwd(), "recommended_pytest_paths.txt");
  let pythonCommand = "python3";
  let pipCommand = "pip3";
  try {
    if (!fs.existsSync(packageDir)) {
      throw new Error(`Python package not found at ${packageDir}`);
    }
    // Create virtual environment (60s timeout to prevent hanging).
    console.log("Creating virtual environment...");
    await execWithTimeout("python3", ["-m", "venv", venvPath], { silent: true }, 60000);
    pythonCommand = path.join(venvPath, "bin", "python");
    pipCommand = path.join(venvPath, "bin", "pip");
    // Install regex dependency (60s timeout).
    console.log("Installing dependencies...");
    await execWithTimeout(pipCommand, ["install", "regex", "-q"], { silent: true }, 60000);

    // Build command arguments for `python -m test_selector ...`.
    const args = ["-m", "test_selector", "--repo", params.repo];
    if (params.githubPr) {
      args.push("--github-pr", params.githubPr);
    }
    if (params.gitcodePr) {
      args.push("--gitcode-pr", params.gitcodePr);
    }
    args.push("--source-dir", params.sourceDir);
    args.push("--map-file", params.mapFile);
    args.push("--coverage-dir", params.coverageDir);
    args.push("--min-affected", params.minAffected.toString());
    if (params.buildMap) {
      args.push("--build-map");
    }
    if (params.dedup) {
      args.push("--dedup");
    }
    if (params.enableLineMatch) {
      args.push("--enable-line-match");
    } else {
      args.push("--disable-line-match");
    }
    if (params.enableFunctionMatch) {
      args.push("--enable-function-match");
    } else {
      args.push("--disable-function-match");
    }
    if (params.skipImports) {
      args.push("--skip-imports");
    }
    // Make dist/ importable so `python -m test_selector` finds the package.
    const env = {
      ...process.env,
      PYTHONPATH: distDir,
    };
    // Execute Python script (parameterized exec, not shell; validated inputs).
    console.log("Running precision test selector...");
    await execWithTimeout(
      pythonCommand,
      args,
      { env, cwd: process.cwd() },
      PYTHON_EXEC_TIMEOUT_SECONDS * 1000,
    );

    // Read output file produced by the Python CLI.
    if (fs.existsSync(outputFile)) {
      const content = fs.readFileSync(outputFile, "utf-8");
      const testList = content
        .trim()
        .split("\n")
        .filter((line) => line.trim() !== "");
      fs.writeFileSync(workspaceOutput, content);
      return {
        success: true,
        testListFile: workspaceOutput,
        testCount: testList.length,
      };
    }
    console.log("No test cases recommended");
    return { success: true, testListFile: workspaceOutput, testCount: 0 };
  } catch (error) {
    const err = error as Error;
    throw new Error(`Failed to run precision test selector: ${err.message}`);
  }
}

/**
 * Main function.
 */
async function run(): Promise<void> {
  try {
    console.log("=".repeat(60));
    console.log("Starting Precision Test Selector...");
    console.log("=".repeat(60));

    // Get input parameters.
    core.startGroup("Step 1: Get input parameters");
    const repo = core.getInput("repo", { required: false }) || "vllm_ascend";
    const githubPr = core.getInput("github-pr", { required: false });
    const gitcodePr = core.getInput("gitcode-pr", { required: false });
    const sourceDir = core.getInput("source-dir", { required: false }) || "covstub";
    const mapFile = core.getInput("map-file", { required: false }) || "test_case_map.json";
    const coverageDir = core.getInput("coverage-dir", { required: false }) || "coverage";
    const buildMap = core.getInput("build-map", { required: false }) === "true";
    const minAffected = parseInt(
      core.getInput("min-affected", { required: false }) || "1",
      10,
    );
    const dedup = core.getInput("dedup", { required: false }) === "true";
    const enableLineMatch =
      core.getInput("enable-line-match", { required: false }) !== "false";
    const enableFunctionMatch =
      core.getInput("enable-function-match", { required: false }) !== "false";
    const skipImports = core.getInput("skip-imports", { required: false }) === "true";
    console.log("Input parameters:");
    console.log(`  - repo: ${repo}`);
    console.log(`  - github-pr: ${githubPr ? "(provided)" : "(not specified)"}`);
    console.log(`  - gitcode-pr: ${gitcodePr ? "(provided)" : "(not specified)"}`);
    console.log("  - source-dir: (validated)");
    console.log("  - map-file: (validated)");
    console.log("  - coverage-dir: (validated)");
    console.log(`  - build-map: ${buildMap}`);
    console.log(`  - min-affected: ${minAffected}`);
    console.log(`  - dedup: ${dedup}`);
    console.log(`  - enable-line-match: ${enableLineMatch}`);
    console.log(`  - enable-function-match: ${enableFunctionMatch}`);
    console.log(`  - skip-imports: ${skipImports}`);
    // Mask sensitive inputs.
    if (sourceDir) core.setSecret(sourceDir);
    if (coverageDir) core.setSecret(coverageDir);
    if (mapFile) core.setSecret(mapFile);
    core.endGroup();

    // github-pr and gitcode-pr are mutually exclusive.
    if (githubPr && gitcodePr) {
      throw new Error(
        "github-pr and gitcode-pr are mutually exclusive; specify only one",
      );
    }

    // Validate paths (resolve relative paths against the workspace).
    core.startGroup("Step 2: Validate paths");
    const validatedSourceDir = validatePath(sourceDir, "source-dir");
    const validatedCoverageDir = validatePath(coverageDir, "coverage-dir");
    const validatedMapFile = path.resolve(mapFile);
    console.log(`Validated source-dir: ${validatedSourceDir}`);
    console.log(`Validated coverage-dir: ${validatedCoverageDir}`);
    console.log(`Validated map-file: ${validatedMapFile}`);
    core.endGroup();

    // Execute precision test selector.
    core.startGroup("Step 3: Execute precision test selector");
    const result = await runPrecisionTest({
      repo,
      githubPr,
      gitcodePr,
      sourceDir: validatedSourceDir,
      mapFile: validatedMapFile,
      coverageDir: validatedCoverageDir,
      buildMap,
      minAffected,
      dedup,
      enableLineMatch,
      enableFunctionMatch,
      skipImports,
    });
    core.endGroup();

    // Print test list file (declared outputs intentionally omitted; print only).
    core.startGroup("Step 4: Test list file");
    console.log(`test-list-file=${result.testListFile}`);
    console.log(`test-count=${result.testCount}`);
    core.endGroup();

    console.log("=".repeat(60));
    console.log("Precision test selection completed.");
    console.log("=".repeat(60));
  } catch (error) {
    const err = error as Error;
    core.error("=".repeat(60));
    core.error(`Precision test selection failed: ${err.message}`);
    core.error("=".repeat(60));
    if (err.stack) {
      core.error(`Stack trace:\n${err.stack}`);
    }
    core.setFailed(err.message);
  }
}

// Run main function.
run();
