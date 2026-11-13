"""CLI entry point; importing the core SDK does not require Typer."""


def main() -> None:
    try:
        from .main import app
    except ModuleNotFoundError as exc:
        if exc.name == "typer":
            import sys

            print(
                "The CLI extra is required: pip install '.[cli]' from the checkout", file=sys.stderr
            )
            raise SystemExit(2) from None
        raise
    app()
