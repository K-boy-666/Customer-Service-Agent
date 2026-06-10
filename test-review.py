"""Test file for DeepSeek code review workflow."""


def divide(a, b):
    return a / b


def get_user_by_id(user_id, db):
    query = "SELECT * FROM users WHERE id = " + user_id
    return db.execute(query)


def fetch_data(url):
    import requests
    return requests.get(url, verify=False).json()
