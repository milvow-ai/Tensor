import { type ChildProcess, spawn } from "node:child_process";
import { join } from "node:path";

const NEXT_BIN = join(process.cwd(), "node_modules", "next", "dist", "bin", "next");

export interface Server {
  url: string;
  output: () => string;
  stop: () => Promise<void>;
  exited: Promise<number | null>;
}

/** Starts a second production server (the build in .next) with its own environment, for tests of other modes. */
export function startServer(port: number, env: Record<string, string | undefined>): Server {
  const merged: NodeJS.ProcessEnv = { ...process.env, ...env };
  // An explicit undefined removes the variable (spawn would otherwise inherit it from this process).
  for (const [key, value] of Object.entries(env)) if (value === undefined) delete merged[key];

  const child: ChildProcess = spawn(process.execPath, [NEXT_BIN, "start", "-p", String(port), "-H", "127.0.0.1"], {
    env: merged,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  child.stdout?.on("data", (chunk: Buffer) => {
    log += chunk.toString();
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    log += chunk.toString();
  });
  const exited = new Promise<number | null>((resolve) => child.on("exit", (code) => resolve(code)));

  return {
    url: `http://127.0.0.1:${port}`,
    output: () => log,
    exited,
    stop: async () => {
      if (child.exitCode === null) child.kill();
      await exited;
    },
  };
}

/** Waits until the server answers, or fails with its log if it exits first. */
export async function waitUntilReady(server: Server, path = "/login", timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let exitedEarly = false;
  void server.exited.then(() => {
    exitedEarly = true;
  });
  while (Date.now() < deadline) {
    if (exitedEarly) throw new Error(`server exited before it was ready:\n${server.output()}`);
    try {
      const response = await fetch(`${server.url}${path}`, { redirect: "manual" });
      if (response.status < 500) return;
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`server did not become ready:\n${server.output()}`);
}
