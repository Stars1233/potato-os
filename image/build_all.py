#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


def info(message: str) -> None:
    print(f"[potato-image] {message}", flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    info(f"$ {shlex.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd, env=env)


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def ensure_docker_daemon_ready() -> None:
    probe = run_capture(["docker", "info"])
    if probe.returncode == 0:
        return

    context = run_capture(["docker", "context", "show"])
    active_context = context.stdout.strip() if context.returncode == 0 else ""
    if active_context and active_context != "default":
        info(
            f'Docker context "{active_context}" is unavailable. '
            'Trying fallback: docker context use default'
        )
        switched = run_capture(["docker", "context", "use", "default"])
        if switched.returncode == 0:
            probe_after_switch = run_capture(["docker", "info"])
            if probe_after_switch.returncode == 0:
                return
            probe = probe_after_switch

    err = (probe.stderr or probe.stdout).strip()
    message = (
        "Docker daemon is not reachable. Start Docker Desktop or run 'colima start', "
        "then rerun ./image/build-all.sh.\n"
        f"Docker error: {err or 'unknown error'}"
    )
    raise RuntimeError(message)


def setup_docker_runtime() -> None:
    if sys.platform != "darwin":
        return

    if run_capture(["docker", "info"]).returncode == 0:
        return

    if run_capture(["colima", "status"]).returncode != 0:
        if run_capture(["brew", "--version"]).returncode != 0:
            raise RuntimeError("Homebrew is required for --setup-docker on macOS. Install brew first.")
        info("Installing Docker CLI + Colima via Homebrew.")
        run(["brew", "install", "docker", "colima"])

    info("Starting Colima Docker runtime.")
    run(["colima", "start"])


def ensure_pi_gen_checkout(pi_gen_dir: Path, repo_url: str, branch: str, update: bool) -> None:
    git_dir = pi_gen_dir / ".git"
    if git_dir.exists():
        if not update:
            info(f"Using existing pi-gen checkout: {pi_gen_dir}")
            return
        run(["git", "-C", str(pi_gen_dir), "fetch", "origin", branch, "--depth", "1"])
        run(["git", "-C", str(pi_gen_dir), "checkout", branch])
        run(["git", "-C", str(pi_gen_dir), "pull", "--ff-only", "origin", branch])
        return

    pi_gen_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(pi_gen_dir)])


def build_variant(repo_root: Path, variant: str, args: argparse.Namespace) -> None:
    script_path = repo_root / "image" / f"build-{variant}.sh"
    if not script_path.exists():
        raise FileNotFoundError(f"Missing build script: {script_path}")

    env = os.environ.copy()
    env["POTATO_PI_GEN_DIR"] = str(args.pi_gen_dir)
    env["POTATO_SSH_USER"] = args.ssh_user
    env["POTATO_SSH_PASSWORD"] = args.ssh_password
    env["POTATO_HOSTNAME"] = args.hostname

    if args.output_dir:
        env["POTATO_IMAGE_OUTPUT_DIR"] = str(args.output_dir)
    if args.build_root:
        env["POTATO_IMAGE_BUILD_ROOT"] = str(args.build_root)
    if args.cache_dir:
        env["POTATO_IMAGE_CACHE_DIR"] = str(args.cache_dir)
    if args.dry_run:
        env["POTATO_IMAGE_DRY_RUN"] = "1"
    if args.pi_gen_use_docker:
        env["POTATO_PI_GEN_USE_DOCKER"] = "1"

    if args.model_url:
        env["POTATO_MODEL_URL"] = args.model_url
    if args.full_model_path:
        env["POTATO_FULL_MODEL_PATH"] = str(args.full_model_path)
    if args.full_mmproj_path:
        env["POTATO_FULL_MMPROJ_PATH"] = str(args.full_mmproj_path)

    run([str(script_path)], cwd=repo_root, env=env)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-command builder for Potato single-flash images. "
            "It bootstraps/updates pi-gen and runs lite/full image builders."
        )
    )
    parser.add_argument(
        "--variant",
        choices=("lite", "full", "both"),
        default="both",
        help="Which image variant to build (default: both).",
    )
    parser.add_argument(
        "--pi-gen-dir",
        default=os.getenv("POTATO_PI_GEN_DIR", ".cache/pi-gen-arm64"),
        help="Path to local pi-gen checkout (default: .cache/pi-gen-arm64 or POTATO_PI_GEN_DIR).",
    )
    parser.add_argument(
        "--pi-gen-repo",
        default="https://github.com/RPi-Distro/pi-gen.git",
        help="pi-gen git repository URL.",
    )
    parser.add_argument("--pi-gen-branch", default="arm64", help="pi-gen branch to use (default: arm64).")
    parser.add_argument(
        "--no-update-pi-gen",
        action="store_true",
        help="Do not fetch/pull if pi-gen checkout already exists.",
    )
    parser.add_argument(
        "--pi-gen-use-docker",
        action="store_true",
        help="Run pi-gen using build-docker.sh instead of build.sh.",
    )
    parser.add_argument(
        "--setup-docker",
        action="store_true",
        help="On macOS, install/start Docker runtime via Homebrew+Colima before building.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Prepare stage/config only, skip actual image build.")
    parser.add_argument("--hostname", default=os.getenv("POTATO_HOSTNAME", "potato"), help="Image hostname.")
    parser.add_argument("--ssh-user", default=os.getenv("POTATO_SSH_USER", "pi"), help="Default SSH username.")
    parser.add_argument(
        "--ssh-password",
        default=os.getenv("POTATO_SSH_PASSWORD", "raspberry"),
        help="Default SSH password.",
    )
    parser.add_argument("--output-dir", default=os.getenv("POTATO_IMAGE_OUTPUT_DIR"), help="Output artifact directory.")
    parser.add_argument("--build-root", default=os.getenv("POTATO_IMAGE_BUILD_ROOT"), help="Temporary build root.")
    parser.add_argument("--cache-dir", default=os.getenv("POTATO_IMAGE_CACHE_DIR"), help="Download cache directory.")
    parser.add_argument(
        "--model-url",
        default=os.getenv("POTATO_MODEL_URL"),
        help="Model URL override (used by build scripts when download is needed).",
    )
    parser.add_argument(
        "--full-model-path",
        default=os.getenv("POTATO_FULL_MODEL_PATH"),
        help="Path to local model file for full image variant.",
    )
    parser.add_argument(
        "--full-mmproj-path",
        default=os.getenv("POTATO_FULL_MMPROJ_PATH"),
        help="Path to local mmproj file for full image variant.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        repo_root = Path(__file__).resolve().parents[1]

        pi_gen_dir = Path(args.pi_gen_dir).expanduser().resolve()
        args.pi_gen_dir = pi_gen_dir

        if args.output_dir:
            args.output_dir = Path(args.output_dir).expanduser().resolve()
        if args.build_root:
            args.build_root = Path(args.build_root).expanduser().resolve()
        if args.cache_dir:
            args.cache_dir = Path(args.cache_dir).expanduser().resolve()
        if args.full_model_path:
            args.full_model_path = Path(args.full_model_path).expanduser().resolve()
        if args.full_mmproj_path:
            args.full_mmproj_path = Path(args.full_mmproj_path).expanduser().resolve()

        info(f"Repository root: {repo_root}")
        info(f"pi-gen checkout: {pi_gen_dir}")

        ensure_pi_gen_checkout(
            pi_gen_dir,
            repo_url=args.pi_gen_repo,
            branch=args.pi_gen_branch,
            update=not args.no_update_pi_gen,
        )

        if sys.platform != "linux" and not args.dry_run and not args.pi_gen_use_docker:
            args.pi_gen_use_docker = True
            info("Non-Linux host detected; enabling --pi-gen-use-docker automatically.")
        if args.pi_gen_use_docker and not args.dry_run:
            if args.setup_docker:
                setup_docker_runtime()
            ensure_docker_daemon_ready()

        variants = ["lite", "full"] if args.variant == "both" else [args.variant]
        for variant in variants:
            info(f"Starting variant: {variant}")
            build_variant(repo_root, variant, args)

        info("Done.")
        return 0
    except RuntimeError as exc:
        info(str(exc))
        return 1
    except subprocess.CalledProcessError as exc:
        info(f"Build command failed ({exc.returncode}): {shlex.join(exc.cmd)}")
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
