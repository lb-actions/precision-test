/**
 * Precision Test Selector - GitHub Action Entry Point
 *
 * Coverage-based precision test selector supporting line, function, and file granularity.
 */
import * as core from "@actions/core";
import * as exec from "@actions/exec";
import * as path from "path";
import * as fs from "fs";
import * as os from "os";

/**
 * Security: Python subprocess execution timeout in seconds (prevents DoS).
 * Can be overridden via PYTHON_EXEC_TIMEOUT_SECONDS environment variable.
 */
const PYTHON_EXEC_TIMEOUT_SECONDS = parseInt(
  process.env.PYTHON_EXEC_TIMEOUT_SECONDS || "600",
  10
);

/**
 * Execute a command with timeout.
 * Wraps exec.exec() with timeout functionality.
 */
async function execWithTimeout(
  commandLine: string,
  args: string[],
  options: exec.ExecOptions,
  timeoutMs: number
): Promise<number> {
  return new Promise((resolve, reject) => {
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
 * Parameters for precision test selector.
 */
interface PrecisionTestParams {
  githubPr: string;
  sourceDir: string;
  mapFile: string;
  coverageDir: string;
  buildMap: boolean;
  minAffected: number;
  dedup: boolean;
  enableLineMatch: boolean;
  enableFunctionMatch: boolean;
  enableFileMatch: boolean;
  skipImports: boolean;
  repoName: string;
}

/**
 * Sanitize output value for CI output file.
 */
function sanitizeOutputValue(value: string): string {
  if (!value) return "";
  return value
    .replace(/\\/g, "\\\\")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/=/g, "\\=");
}

/**
 * Validate path for security.
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

/**
 * Execute precision test selector.
 */
async function runPrecisionTest(
  params: PrecisionTestParams
): Promise<{ success: boolean; testListFile: string; testCount: number }> {
  const venvPath = path.join(os.tmpdir(), `precision_test_venv_${process.pid}`);
  const selectorPy = path.resolve(__dirname, "selector.py");
  const outputFile = path.join(process.cwd(), "recommended_pytest_paths.txt");

  let pythonCommand = "python3";
  let pipCommand = "pip3";

  try {
    // Create virtual environment
    // Security: 60 seconds timeout to prevent hanging
    console.log("Creating virtual environment...");
    await execWithTimeout("python3", ["-m", "venv", venvPath], { silent: true }, 60000);

    pythonCommand = path.join(venvPath, "bin", "python");
    pipCommand = path.join(venvPath, "bin", "pip");

    // Install regex dependency
    // Security: 60 seconds timeout to prevent hanging
    console.log("Installing dependencies...");
    await execWithTimeout(pipCommand, ["install", "regex", "-q"], { silent: true }, 60000);

    // Build command arguments
    const args: string[] = [selectorPy];

    if (params.githubPr) {
      args.push("--github-pr", params.githubPr);
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

    if (params.enableFileMatch) {
      args.push("--enable-file-match");
    } else {
      args.push("--disable-file-match");
    }

    if (params.skipImports) {
      args.push("--skip-imports");
    }

    // Set environment variable for repo name
    const env = {
      ...process.env,
      REPO_NAME: params.repoName,
    };

    // Execute Python script
    // Security: Using parameterized exec call (not shell) with validated inputs
    // Security: Added timeout to prevent DoS attacks (FINDING-001)
    console.log("Running precision test selector...");

    await execWithTimeout(
      pythonCommand,
      args,
      {
        env,
        cwd: process.cwd(),
      },
      PYTHON_EXEC_TIMEOUT_SECONDS * 1000
    );

    // Read output file
    if (fs.existsSync(outputFile)) {
      const content = fs.readFileSync(outputFile, "utf-8");
      const testList = content.trim().split("\n").filter((line) => line.trim() !== "");
      return {
        success: true,
        testListFile: outputFile,
        testCount: testList.length,
      };
    } else {
      console.log("No test cases recommended");
      return {
        success: true,
        testListFile: outputFile,
        testCount: 0,
      };
    }
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

    // Get input parameters
    core.startGroup("Step 1: Get input parameters");
    const githubPr = core.getInput("github-pr", { required: false });
    const sourceDir = core.getInput("source-dir", { required: false }) || "covstub";
    const mapFile = core.getInput("map-file", { required: false }) || "test_case_map.json";
    const coverageDir = core.getInput("coverage-dir", { required: false }) || "coverage";
    const buildMap = core.getInput("build-map", { required: false }) === "true";
    const minAffected = parseInt(core.getInput("min-affected", { required: false }) || "1", 10);
    const dedup = core.getInput("dedup", { required: false }) === "true";
    const enableLineMatch = core.getInput("enable-line-match", { required: false }) !== "false";
    const enableFunctionMatch =
      core.getInput("enable-function-match", { required: false }) !== "false";
    const enableFileMatch = core.getInput("enable-file-match", { required: false }) !== "false";
    const skipImports = core.getInput("skip-imports", { required: false }) === "true";
    const repoName = core.getInput("repo-name", { required: false }) || "vllm_ascend";

    console.log("Input parameters:");
    console.log(`  - github-pr: ${githubPr ? "(provided)" : "(not specified)"}`);
    console.log(`  - source-dir: (validated)`);
    console.log(`  - map-file: (validated)`);
    console.log(`  - coverage-dir: (validated)`);
    console.log(`  - build-map: ${buildMap}`);
    console.log(`  - min-affected: ${minAffected}`);
    console.log(`  - dedup: ${dedup}`);
    console.log(`  - enable-line-match: ${enableLineMatch}`);
    console.log(`  - enable-function-match: ${enableFunctionMatch}`);
    console.log(`  - enable-file-match: ${enableFileMatch}`);
    console.log(`  - skip-imports: ${skipImports}`);
    console.log(`  - repo-name: ${repoName}`);

    // Mask sensitive inputs
    if (sourceDir) core.setSecret(sourceDir);
    if (coverageDir) core.setSecret(coverageDir);
    if (mapFile) core.setSecret(mapFile);
    core.endGroup();

    // Validate paths
    core.startGroup("Step 2: Validate paths");
    const validatedSourceDir = validatePath(sourceDir, "source-dir");
    const validatedCoverageDir = validatePath(coverageDir, "coverage-dir");
    console.log(`Validated source-dir: ${validatedSourceDir}`);
    console.log(`Validated coverage-dir: ${validatedCoverageDir}`);
    core.endGroup();

    // Execute precision test selector
    core.startGroup("Step 3: Execute precision test selector");
    const result = await runPrecisionTest({
      githubPr,
      sourceDir: validatedSourceDir,
      mapFile,
      coverageDir: validatedCoverageDir,
      buildMap,
      minAffected,
      dedup,
      enableLineMatch,
      enableFunctionMatch,
      enableFileMatch,
      skipImports,
      repoName,
    });
    core.endGroup();

    // Set outputs
    core.startGroup("Step 4: Set outputs");
    core.setOutput("test-list-file", result.testListFile);
    core.setOutput("test-count", result.testCount.toString());
    console.log(`Output: test-list-file=${result.testListFile}`);
    console.log(`Output: test-count=${result.testCount}`);

    // Write to ATOMGIT_OUTPUT for GitHub compatibility
    const atomgitOutputPath = process.env.ATOMGIT_OUTPUT;
    if (atomgitOutputPath) {
      const sanitizedFile = sanitizeOutputValue(result.testListFile);
      const sanitizedCount = sanitizeOutputValue(result.testCount.toString());
      const outputContent =
        `test-list-file=${sanitizedFile}\ntest-count=${sanitizedCount}\n`;
      fs.appendFileSync(atomgitOutputPath, outputContent);
    }
    core.endGroup();

    console.log("=".repeat(60));
    console.log(`Precision test selection completed. Found ${result.testCount} tests.`);
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

// Run main function
run();