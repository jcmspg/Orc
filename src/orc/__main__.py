"""Entry point for `python -m orc` and the `orc` console script."""

import argparse
import asyncio

from orc import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="orc", description="Orchestrate AI agent peons.")
    parser.add_argument("--version", action="version", version=f"orc {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the engine server the desktop app connects to")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=47820)

    sub.add_parser("build-image", help="build the orc-peon Docker image (the black box)")

    args = parser.parse_args()
    if args.cmd == "serve":
        import uvicorn

        from orc.sandbox import image_exists
        from orc.server import create_app

        import shutil

        from orc.gitops import GIT_MISSING
        from orc.sandbox import DOCKER_MISSING

        if not shutil.which("git"):
            print(f"warning: {GIT_MISSING}", flush=True)
        if not shutil.which("docker"):
            print(f"warning: {DOCKER_MISSING}", flush=True)
        elif not asyncio.run(image_exists()):
            print("note: the orc-peon image is missing; run `orc build-image` before giving orders.", flush=True)
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    elif args.cmd == "build-image":
        from orc.sandbox import build_image

        asyncio.run(build_image(on_line=print))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
