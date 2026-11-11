#!/usr/bin/env python3
"""Build one private console under its own OS identity; no provider keys enter Node's environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

APP = Path("/opt/harness-farm")
ETC = Path("/etc/harness-farm/instances")
STATE = Path("/var/lib/harness-farm-consoles")
NAME = re.compile(r"[a-z][a-z0-9-]{0,19}\Z")


def worker(name: str) -> None:
    import pwd

    account = pwd.getpwnam(f"hfui-{name}")
    if os.geteuid() != account.pw_uid or os.geteuid() == 0:
        raise ValueError("console builds must run as the dedicated non-root console user")
    home = STATE / name
    public = json.loads((home / "public-settings.json").read_text())
    if not isinstance(public.get("url"), str) or not isinstance(public.get("key"), str):
        raise ValueError("missing public console settings")
    stage = home / f"build-{uuid4().hex}"
    shutil.copytree(
        APP / "console",
        stage,
        ignore=shutil.ignore_patterns(
            "node_modules", ".next", ".vercel", ".env*", "test-results", "playwright-report", ".git", "e2e"
        ),
    )
    (stage / "node_modules").symlink_to(APP / "console/node_modules", target_is_directory=True)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NODE_ENV": "production",
        "FARM_DATA_SOURCE": "supabase",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PUBLIC_SUPABASE_URL": public["url"],
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": public["key"],
    }
    subprocess.run(
        ["/usr/bin/node", str(APP / "console/node_modules/next/dist/bin/next"), "build", "--webpack"],
        cwd=stage,
        env=env,
        check=True,
        timeout=1200,
    )
    link = home / f"activate-{uuid4().hex}"
    link.symlink_to(stage.name, target_is_directory=True)
    os.replace(link, home / "console")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not NAME.fullmatch(args.name):
        raise ValueError("invalid instance name")
    if args.worker:
        worker(args.name)
        return
    profile = json.loads((ETC / args.name / "profile.json").read_text())
    port = profile["console_port"]
    if profile["name"] != args.name or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("invalid managed profile")
    if not args.apply:
        print(f"Build console {args.name} as hfui-{args.name}, loopback port {port}; no changes made.")
        return
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("apply requires root on the target server")
    if not (APP / "console/node_modules/next/dist/bin/next").is_file():
        raise ValueError("install the console's frozen pnpm dependencies first")
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--wait",
            "--pipe",
            "--collect",
            f"--uid=hfui-{args.name}",
            f"--gid=hfui-{args.name}",
            "--property=MemoryMax=3G",
            "--property=TasksMax=256",
            "--property=CPUQuota=100%",
            "--property=RuntimeMaxSec=1250",
            "--property=KillMode=control-group",
            "--property=Nice=10",
            "/usr/bin/python3",
            str(Path(__file__).resolve()),
            args.name,
            "--worker",
        ],
        check=True,
        timeout=1300,
    )
    settings = ETC / args.name / "console.env"
    settings.write_text(
        f"NODE_ENV=production\nFARM_DATA_SOURCE=supabase\nNEXT_TELEMETRY_DISABLED=1\n"
        f"FARM_CONSOLE_PORT={port}\nHOME={STATE / args.name}\n"
    )
    settings.chmod(0o600)
    shutil.copyfile(
        APP / "deploy/systemd/harness-farm-console@.service",
        "/etc/systemd/system/harness-farm-console@.service",
    )
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True, timeout=30)
    print("Console built; service was not started or restarted. Configure private TLS before starting it.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Console build refused ({type(exc).__name__}); existing build was preserved.", file=sys.stderr)
        sys.exit(2)
