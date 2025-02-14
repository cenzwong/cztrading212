# main.py
import os
from dotenv import load_dotenv

load_dotenv()

TRADING212_API = os.getenv('TRADING212_API')

from cztrading212 import Trading212
# from cztrading212.stocks import *

def main():
    api_key = TRADING212_API
    env = "demo"  # or "live" or "demo"
    
    # Create the Trading212 instance
    t212 = Trading212(api_key=api_key, environment=env)
    
    # # Fetch pies and orders
    # pies = t212.pies.fetch_all_pies()
    # pies = t212.pies.fetch_a_pie(3708188)
    # orders = t212.orders.fetch_all_orders()
    
    # payload = {
    #     "dividendCashAction": "REINVEST",
    #     "endDate": "2026-08-24T14:15:22Z",
    #     "goal": 0,
    #     "icon": "string",
    #     "instrumentShares": {
    #         "AAPL_US_EQ": 0.4,
    #         "MSFT_US_EQ": 0.4,
    #         "NVDA_US_EQ": 0.2,
    #     },
    #     "name": "NancyPelosi"
    # }

    # print("Pies:", pies)
    # print("Pies:", pies)
    # print("Pies:", t212.pies.update_pie(3708188,payload))
    print(t212.account_data.fetch_account_cash())
    # print(t212.personal_portfolio.fetch_a_specific_position("AAPL_US_EQ"))
    # print(t212.personal_portfolio.fetch_all_open_positions())
    # print(t212.personal_portfolio.search_for_a_specific_position_by_ticker("AAPL_US_EQ"))
    # print("Orders:", orders)

if __name__ == "__main__":
    main()
