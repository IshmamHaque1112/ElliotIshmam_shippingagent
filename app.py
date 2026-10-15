"""Flask app: pages (views.py) and a JSON API (api.py) behind fail-closed sign-in, CSRF and a daily Claude budget."""

import json
import os

from dotenv import load_dotenv
from flask import Flask, g, request
from werkzeug.middleware.proxy_fix import ProxyFix

import api
import config
import limits
import security
import sim
import store
import views

app = Flask(__name__)
load_dotenv()
hops = int(os.environ.get("TRUST_PROXY_HOPS") or 0)   # set to 1 behind one reverse proxy so client addresses and https are read correctly
if hops:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
app.register_blueprint(views.bp)
app.register_blueprint(api.bp)
_loaded_for: str | None = None


@app.before_request
def guard():
    refused = security.require_demo_login()
    if refused is not None:
        return refused
    identity = g.get("identity")
    g._ctx = []
    if identity is None:    # public page (login, health, static files): no per-user state
        return None
    if identity.kind == "session":
        # This signed-in session's own dataset and clock; nobody else's requests can move them.
        g._ctx.append(("dataset", config.use_dataset(identity.dataset)))
        g.sim_state = sim.from_export(store.get_session_sim(identity.sid, identity.dataset))
        g._sim_before = json.dumps(sim.export_of(g.sim_state), sort_keys=True)
        g._ctx.append(("sim", sim.use(g.sim_state)))
        g._ctx.append(("limits", limits.set_identity("sid:" + identity.sid)))
    else:
        g._ctx.append(("limits", limits.set_identity("user:" + identity.user)))
        global _loaded_for  # scripts share one saved simulation state, restored once per database
        path = str(config.db_path())
        if _loaded_for != path:
            sim.load(store.get_setting("sim"))
            _loaded_for = path
    return None


@app.teardown_request
def release_context(_error):
    identity = g.get("identity")
    contexts = g.get("_ctx") or []
    if identity is not None and identity.kind == "session" and g.get("sim_state") is not None:
        after = json.dumps(sim.export_of(g.sim_state), sort_keys=True)
        if after != g.get("_sim_before"):
            store.set_session_sim(identity.sid, g.get("_dataset_at_start", identity.dataset), json.loads(after))
    for name, token in reversed(contexts):
        {"dataset": config.reset_dataset, "sim": sim.release, "limits": limits.reset_identity}[name](token)


app.after_request(security.add_headers)
for code in (400, 403, 404, 405, 500):
    @app.errorhandler(code)
    def _error(error, _code=code):
        if request.path.startswith("/api/"):
            return security.json_error(error)
        return error


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "5000")))
