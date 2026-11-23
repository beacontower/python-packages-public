"""Log in and check that the account works: the first thing to run against a new environment."""

from _common import connect

bt = connect()
bt.check_auth()  # raises with the reason when the address or the account is wrong
print(f"Logged in. The API is at {bt.url}")
