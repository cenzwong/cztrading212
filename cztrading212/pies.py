# trading212/pies.py

import requests

class Pies:
    def __init__(self, trading_instance):
        self.api_key = trading_instance.api_key
        self.environment = trading_instance.environment
        self.base_url = trading_instance.base_url
    
    def fetch_all_pies(self):
        """
        Fetch all pies.
        API Endpoint (assumed): GET /api/v0/equity/pies
        """
        subdirectory = f"/api/v0/equity/pies"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
        return response.json()

    def fetch_a_pie(self, pie_id):
        """
        Fetch details for a specific pie.
        API Endpoint (assumed): GET /api/v0/equity/pies/{id}
        """
        subdirectory = f"/api/v0/equity/pies/{pie_id}"

        url = f"{self.base_url}{subdirectory}"
        headers = {"Authorization": f"{self.api_key}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    
    def update_pie(self, pie_id, payload = None):
        subdirectory = f"/api/v0/equity/pies/{pie_id}"

        url = f"{self.base_url}{subdirectory}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"{self.api_key}"
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

