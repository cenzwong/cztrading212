import requests

class AccountData:
    def __init__(self, trading_instance):
        self.api_key = trading_instance.api_key
        self.environment = trading_instance.environment
        self.base_url = trading_instance.base_url
    
    def fetch_account_cash(self):
        """
        Fetch all pies.
        API Endpoint (assumed): GET /api/v0/equity/account/cash
        """
        subdirectory = f"/api/v0/equity/account/cash"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()

    def fetch_account_metadata(self):
        """
        Fetch all pies.
        API Endpoint (assumed): GET /api/v0/equity/account/info
        """
        subdirectory = f"/api/v0/equity/account/info"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()