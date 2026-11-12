"""Optional MCP adapter entry point."""


def main() -> None:
    try:
        from .server import run
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            import sys

            print(
                "The MCP extra is required: pip install '.[mcp]' from the checkout", file=sys.stderr
            )
            raise SystemExit(2) from None
        raise
    run()
