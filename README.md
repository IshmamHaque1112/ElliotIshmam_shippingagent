# ElliotIshmam_shippingagent
# Shipping Agent

A small, read-only logistics demo. It looks up a shipment in local sample data,
checks the record, and sends valid records to Claude for a dispatcher-facing
classification. It never changes a shipment. A human makes the operational
decision.

## Set up

Use Python 3.10 or newer (Python 3.12 is recommended). From this folder, create
and activate a virtual environment, then install the packages:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create a local `.env` file in this folder with your Anthropic API key:

```dotenv
ANTHROPIC_API_KEY=your_key_here
```

Keep the real key private. `.env` is ignored by Git; never put the key in
`.env.example` or commit it.

For browser access, add a username and a long, unique password to the same
local `.env` file. Keep both values private too:

```dotenv
DEMO_USERNAME=choose_a_username
DEMO_PASSWORD=choose_a_long_password
```

The browser will prompt for these credentials when you open the site. The demo
stops after 10 classification attempts per server run; restarting the server
resets this in-memory cap.

## Run the demo

Start the local web app from the project folder:

```bash
flask --app app run
```

Open `http://127.0.0.1:5000` in your browser. Use **Prepare** for a local check
or **Classify with Claude** for a paid API request.

You can also use the command line:

Prepare a shipment locally. This checks the record and does not call Claude:

```bash
python main.py prepare SHIP-1001
```

Classify a complete shipment with Claude. This sends its sample shipment data
to Anthropic and may incur API charges:

```bash
python main.py classify SHIP-1001
```

For hosting, use `pip install -r requirements.txt` as the build command and
`gunicorn app:app` as the start command. Set `ANTHROPIC_API_KEY`,
`DEMO_USERNAME`, and `DEMO_PASSWORD` in the host's secret environment settings;
do not upload `.env` to GitHub.

Sample tracking numbers:

| Tracking number | Purpose | Expected result |
| --- | --- | --- |
| `SHIP-1001` | Complete, in-transit record | `ON_TRACK` when classified |
| `SHIP-1002` | Complete, delayed record with a weather exception | `NEEDS_ATTENTION` when classified |
| `SHIP-1003` | Intentionally missing its origin | `NEEDS_MANUAL_REVIEW`; no API call |
| `SHIP-9999` | Not in the local dataset | `NEEDS_MANUAL_REVIEW`; no API call |

Only `classify` can call the API, and only after lookup and validation succeed.

## Scope

This demo uses fictional local records. It does not connect to carrier systems,
provide live tracking, authenticate users, or update shipments. Classifications
are advisory and require dispatcher review.
