import requests

class PersonalPortfolio:
    def __init__(self, trading_instance):
        self.api_key = trading_instance.api_key
        self.environment = trading_instance.environment
        self.base_url = trading_instance.base_url
    
    def fetch_all_open_positions(self):
        """
        Fetch all pies.
        API Endpoint (assumed): GET /api/v0/equity/portfolio
        """
        subdirectory = f"/api/v0/equity/portfolio"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()

    def fetch_a_specific_position(self, ticker):
        """
        Fetch all pies.
        API Endpoint (assumed): GET /api/v0/equity/portfolio/{ticker}
        """
        subdirectory = f"/api/v0/equity/portfolio/{ticker}"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()
    
    def search_for_a_specific_position_by_ticker(self, ticker):
        """
        /api/v0/equity/portfolio/ticker
        """
        payload = {
            "ticker": ticker
        }
        subdirectory = f"/api/v0/equity/portfolio/ticker"

        url = f"{self.base_url}{subdirectory}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"{self.api_key}"
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()