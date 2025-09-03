def test_exchange_list(t212_client):
    t212_client.instruments_metadata.exchange_list()

def test_fetch_account_metadata(t212_client):
    t212_client.instruments_metadata.fetch_account_metadata()