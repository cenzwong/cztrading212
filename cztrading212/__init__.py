# trading212/__init__.py

from .pies import Pies
from .account_data import AccountData
from .personal_portfolio import PersonalPortfolio

class Trading212:
    def __init__(self, api_key, environment="demo"):
        self.api_key = api_key
        self.environment = environment
        self.base_url = f"https://{environment}.trading212.com"

        # Instantiate submodules, passing the current instance for shared configuration
        self.pies = Pies(self)
        self.account_data = AccountData(self)
        self.personal_portfolio = PersonalPortfolio(self)
