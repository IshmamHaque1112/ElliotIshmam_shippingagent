# Shipping Agent
### A Human-Supervised AI Agent for Logistics Operations

Shipping Agent is an applied AI engineering project exploring how AI agents can help logistics dispatchers identify shipments that require attention.

The system retrieves shipment records, validates the available information, and uses Anthropic's Claude API to classify valid shipments. Records that are missing or have insufficient information are routed to manual review instead of being sent to the model. 

The goal is to reduce repetitive tracking checks while keeping operational decisions under human control.

**Core principle:** The AI classifies shipment information; the surrounding application controls when the AI is called, what information it receives, and how its output is handled.

> **Project status:** Working demonstration using fictional, locally stored shipment data. Live carrier integrations and production deployment are outside the current scope.

## What It Does

A dispatcher provides a tracking number. Shipping Agent retrieves the corresponding record, validates it, and determines the appropriate classification path.

| Classification | Meaning |
|---|---|
| `ON_TRACK` | The available shipment information indicates normal progress. |
| `NEEDS_ATTENTION` | The shipment information indicates an exception requiring attention. |
| `NEEDS_MANUAL_REVIEW` | The record is missing, incomplete, or unsuitable for automated classification. |

The classifications are advisory. A human dispatcher remains responsible for interpreting the results and deciding what action to take.

## How It Works

```text
Tracking Number
       |
       v
Shipment Lookup
       |
       v
Data Validation
       |
       +----------------------------+
       |                            |
       v                            v
Incomplete or Missing         Valid Record
       |                            |
       v                            v
Manual Review              Claude API Request
No AI Call                         |
                                   v
                           AI Classification
                                   |
                                   v
                            Human Review
```

### 1. Retrieve shipment data

The application looks up a tracking number in a local dataset of fictional shipment records.

Using local data makes the demonstration reproducible without requiring access to external carrier systems.

### 2. Validate the record

The application checks whether the shipment record contains sufficient information for classification.

Missing or unsuitable records are routed to manual review before an AI request is made.

This prevents the model from being used to compensate for missing evidence by guessing.

### 3. Classify valid shipments

When a record passes validation, the application sends its shipment information to Anthropic's Claude API.

Claude classifies the shipment as either `ON_TRACK` or `NEEDS_ATTENTION`.

The model is responsible for classification, not for controlling the entire workflow or making operational decisions.

### 4. Keep operational decisions with humans

Shipping Agent provides a classification to help a dispatcher identify shipments that may need attention.

It does not reroute freight, contact customers, or modify shipment records.

## Engineering Approach

This project explores practical challenges involved in building AI-powered software beyond simply connecting an application to a language model.

**Separation of concerns**

Shipment retrieval, validation, model interaction, and application execution are organized into distinct components. This makes the system easier to understand, debug, test, and extend.

**Deterministic controls around probabilistic AI**

The application determines whether a shipment is eligible for AI classification before making a model request. Incomplete records follow a defined manual-review path.

**Controlled model invocation**

The model is used for a specific task rather than being given unrestricted control over the application. This also avoids unnecessary API calls for records that cannot be classified.

**Human-in-the-loop design**

AI classifications support the dispatcher but do not replace human judgment or authorize operational actions.

**Secure configuration**

The Anthropic API key and browser-demo credentials are supplied through local environment variables rather than hardcoded into the source code. Secrets are excluded from version control.

Together, these choices demonstrate an important principle of applied AI engineering: reliable AI systems depend on the software surrounding the model, including its validation rules, execution flow, and operational boundaries.

## Technology Stack

- **Python** — application logic and workflow implementation.
- **Anthropic Claude API** — shipment classification.
- **Flask** — local web interface.
- **python-dotenv** — environment-variable configuration.
- **pytest** — automated testing.

## Getting Started

### Prerequisites

- Python 3.10 or newer; Python 3.12 is recommended.
- An Anthropic API key with API access and available usage.
- Git.
- A terminal and code editor.

### 1. Clone the repository

```bash
git clone https://github.com/IshmamHaque1112/ElliotIshmam_shippingagent.git
cd ElliotIshmam_shippingagent
```

### 2. Create a virtual environment

Using Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

A virtual environment isolates this project's Python dependencies from other projects on your computer.

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```dotenv
ANTHROPIC_API_KEY=your_anthropic_api_key
DEMO_USERNAME=choose_a_username
DEMO_PASSWORD=choose_a_long_unique_password
```

Replace the placeholder values with your actual credentials.

The API key must come from the Anthropic API platform. A Claude chat subscription does not automatically provide API access.

**Security requirements:**

- Keep `.env` private and never commit it to Git.
- Keep `.env.example` limited to placeholder values.
- Configure credentials through your hosting provider's secret environment settings when deploying.
- Use fictional shipment data during development.

## Running the Demo

### Web interface

Start the local Flask application:

```bash
flask --app app run
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

The interface provides two workflows:

**Prepare**

Looks up and validates a shipment locally without calling Claude.

**Classify with Claude**

Sends a valid shipment record to Anthropic for classification. This makes an external API request and may incur charges.

The demonstration stops after 10 classification attempts per server run. This limit is held in memory and resets when the server restarts.

### Command-line interface

Shipping Agent also supports direct execution from the terminal.

Prepare a shipment without making an AI request:

```bash
python main.py prepare SHIP-1001
```

Classify a valid shipment using Claude:

```bash
python main.py classify SHIP-1001
```

Only the classification workflow can call the API, and only after shipment lookup and validation succeed.

## Sample Shipment Records

The local dataset includes records designed to demonstrate different outcomes.

| Tracking number | Scenario | Expected behavior |
|---|---|---|
| `SHIP-1001` | Complete, in-transit shipment | Eligible for Claude classification; expected to be `ON_TRACK`. |
| `SHIP-1002` | Complete shipment with a weather-related delay | Eligible for Claude classification; expected to be `NEEDS_ATTENTION`. |
| `SHIP-1003` | Shipment missing its origin | `NEEDS_MANUAL_REVIEW`; no API call. |
| `SHIP-9999` | Tracking number not found | `NEEDS_MANUAL_REVIEW`; no API call. |

These scenarios illustrate why data validation should precede model invocation.

## Deployment

The application can be configured for hosting with Gunicorn.

**Build command:**

```bash
pip install -r requirements.txt
```

**Start command:**

```bash
gunicorn app:app
```

Configure the following environment variables through the hosting provider's secret settings:

- `ANTHROPIC_API_KEY`
- `DEMO_USERNAME`
- `DEMO_PASSWORD`

Do not upload `.env` to the hosting platform or commit it to the repository.

The current demonstration is not a production-hardened logistics service. Before handling real customer data or supporting operational workflows, additional work would be needed to evaluate authentication, persistent usage limits, monitoring, error handling, privacy, and reliability.

## Scope and Limitations

Shipping Agent currently:

- Uses fictional shipment records stored locally.
- Does not connect to live carrier tracking systems.
- Uses Claude for advisory classification.
- Routes missing or unsuitable records to manual review.
- Does not modify shipments or execute operational actions.
- Uses an in-memory limit for classification attempts.

The demonstration focuses on the design and implementation of a controlled AI workflow rather than a complete logistics platform.

## Project Objective

Shipping Agent explores a practical question in applied AI engineering:

**How can a language model support a real business workflow without making the surrounding system unpredictable or surrendering operational control to the model?**

The project emphasizes the engineering decisions around AI integration: validating information before inference, limiting the model's responsibility, controlling when external API calls occur, and keeping human judgment in the loop.

The broader goal is to build practical experience designing, implementing, and evaluating AI-powered systems that can support real business operations.