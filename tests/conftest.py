import pytest

@pytest.fixture
def t212_client():
    import os
    from dotenv import load_dotenv

    load_dotenv()

    TRADING212_API = os.getenv('TRADING212_API')

    from cztrading212.trading212_client import Trading212Client, Trading212Config
    config = Trading212Config(
        api_key=TRADING212_API,
        base_url="https://demo.trading212.com",
        environment="demo",        # 或 "demo"
        timeout_secs=10.0
    )
    cli = Trading212Client(config)
    return cli