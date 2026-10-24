# /app.py

from flask import Flask

from auth import init_auth


app = Flask(__name__)

# Connect authentication routes, rate limits, and security headers.
init_auth(app)


if __name__ == "__main__":
    app.run(debug=True)