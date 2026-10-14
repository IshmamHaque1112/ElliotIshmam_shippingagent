"""Flask app: pages (views.py) and a JSON API (api.py) behind fail-closed basic auth, CSRF and a usage cap."""

import os

from dotenv import load_dotenv
from flask import Flask, request

import api
import config
import security
import sim
import store
import views

app = Flask(__name__)
load_dotenv()
app.register_blueprint(views.bp)
app.register_blueprint(api.bp)
_loaded_for: str | None = None


@app.before_request
def guard():
    refused = security.require_demo_login()
    if refused is not None:
        return refused
    global _loaded_for  # restore the saved simulation state once per database
    path = str(config.db_path())
    if _loaded_for != path:
        sim.load(store.get_setting("sim"))
        _loaded_for = path
    return None


app.after_request(security.add_headers)
for code in (400, 403, 404, 405, 500):
    @app.errorhandler(code)
    def _error(error, _code=code):
        if request.path.startswith("/api/"):
            return security.json_error(error)
        return error


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "5000")))
