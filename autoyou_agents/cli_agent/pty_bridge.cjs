// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-B-726c79207375627461736b20-d7e841b7c56b4593be669e72

const packagePath = process.env.AUTOYOU_CLI_NODE_PTY_PACKAGE_PATH || "";
if (!packagePath) {
  process.stderr.write("cli_agent PTY bridge missing AUTOYOU_CLI_NODE_PTY_PACKAGE_PATH\n");
  process.exit(1);
}

let pty;
try {
  pty = require(packagePath);
} catch (error) {
  process.stderr.write(`cli_agent PTY bridge failed to load node-pty from ${packagePath}: ${error}\n`);
  process.exit(1);
}

const args = process.argv.slice(2);
if (args.length < 1) {
  process.stderr.write("cli_agent PTY bridge expected cols rows and command arguments\n");
  process.exit(1);
}

const cols = Math.max(40, parseInt(args[0] || "120", 10) || 120);
const rows = Math.max(10, parseInt(args[1] || "40", 10) || 40);
const shell = args[2];
const shellArgs = args.slice(3);

if (!shell) {
  process.stderr.write("cli_agent PTY bridge missing shell command\n");
  process.exit(1);
}

const ptyProcess = pty.spawn(shell, shellArgs, {
  name: "xterm-color",
  cols,
  rows,
  cwd: process.env.AUTOYOU_CLI_PTY_CWD || process.cwd(),
  env: process.env,
});

ptyProcess.onData((data) => {
  try {
    process.stdout.write(data);
  } catch (error) {
    process.stderr.write(`cli_agent PTY bridge stdout write failed: ${error}\n`);
  }
});

ptyProcess.onExit(({ exitCode }) => {
  process.exitCode = typeof exitCode === "number" ? exitCode : 0;
  process.stdout.end();
});

process.stdin.on("data", (chunk) => {
  try {
    ptyProcess.write(chunk.toString("utf8"));
  } catch (error) {
    process.stderr.write(`cli_agent PTY bridge input write failed: ${error}\n`);
  }
});

process.stdin.on("end", () => {
  try {
    ptyProcess.kill();
  } catch (error) {
    process.stderr.write(`cli_agent PTY bridge kill on stdin end failed: ${error}\n`);
  }
});

process.on("SIGTERM", () => {
  try {
    ptyProcess.kill();
  } catch (error) {
    process.stderr.write(`cli_agent PTY bridge SIGTERM kill failed: ${error}\n`);
  } finally {
    process.exit(0);
  }
});

process.on("SIGINT", () => {
  try {
    ptyProcess.kill();
  } catch (error) {
    process.stderr.write(`cli_agent PTY bridge SIGINT kill failed: ${error}\n`);
  } finally {
    process.exit(0);
  }
});

process.stdin.resume();
