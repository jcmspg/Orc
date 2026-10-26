"""Entry point for `python -m orc` and the `orc` console script."""

from orc import __version__


def main() -> None:
    print(f"orc {__version__}")


if __name__ == "__main__":
    main()
