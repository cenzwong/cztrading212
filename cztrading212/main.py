import os
from dotenv import load_dotenv

load_dotenv()

TRADING212_API = os.getenv('TRADING212_API')

print(TRADING212_API)

import requests

id = "3708188"
url = "https://live.trading212.com/api/v0/equity/pies/" + id

headers = {"Authorization": TRADING212_API}

response = requests.get(url, headers=headers)

data = response.json()
print(data)