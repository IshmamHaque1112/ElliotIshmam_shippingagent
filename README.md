# Shipping Agent

## Overview

Shipping Agent is an applied AI engineering project exploring how AI agents can help logistics dispatchers identify shipments that require attention.

The system retrieves shipment records, validates the available information, and uses Anthropic's Claude API to classify valid shipments. Records with missing or insufficient information are routed to manual review rather than sent to the model.

The goal is to reduce repetitive tracking checks while keeping operational decisions under human control.

## What It Does

A dispatcher provides a tracking number. Shipping Agent retrieves the corresponding shipment record, validates the available information, and determines whether the record can be classified by the AI model.

The system produces one of three classifications:

| Classification | Meaning |
|---|---|
| `ON_TRACK` | The available shipment information indicates normal progress. |
| `NEEDS_ATTENTION` | The available shipment information indicates a potential issue or exception. |
| `NEEDS_MANUAL_REVIEW` | The shipment record is missing, incomplete, or unsuitable for automated classification. |

These classifications are advisory. A human dispatcher remains responsible for reviewing the results and deciding what action to take.

## How It Works

The application follows a controlled workflow:

```text
Tracking Number
       |
       v
Shipment Lookup
       |
       v
Data Validation
       |
       +--------------------------+
       |                          |
       v                          v
Incomplete or Missing        Valid Record
       |                          |
       v                          v
Manual Review              Claude API Request
No AI Call                       |
                                  v
                           AI Classification
                                  |
                                  v
                             Human Review
```

### 1. Retrieve shipment data

The application looks up the tracking number in a local dataset containing fictional shipment records.

Using local data makes the demonstration reproducible without requiring access to external carrier systems.

### 2. Validate the record

The application checks whether the shipment record contains sufficient information for classification.

If a record is missing, incomplete, or unsuitable, the application routes it to manual review without calling the AI model.

This establishes a clear boundary: the model should classify available evidence rather than compensate for missing information by guessing.

### 3. Classify valid shipments

When a record passes validation, the application sends the relevant shipment information to Anthropic's Claude API.

Claude classifies the shipment as either `ON_TRACK` or `NEEDS_ATTENTION`.

The model is responsible for classification. The surrounding application controls when the model is called and how the workflow proceeds.

### 4. Return the result for human review

Shipping Agent provides a classification to help a dispatcher identify shipments that may require attention.

The application does not reroute freight, contact customers, or modify shipment records. Operational decisions remain with the human dispatcher.

## Engineering Decisions

### Separate application logic from model behavior

Shipment retrieval, data validation, model interaction, and application execution have distinct responsibilities.

This separation makes the system easier to understand, test, debug, and extend.

### Validate data before calling the model

The application checks whether a shipment record is suitable for classification before making an AI request.

Records that fail validation follow a defined manual-review path instead of being sent to the model.

### Control when the model is invoked

Claude performs a specific classification task rather than controlling the entire application workflow.

Keeping workflow decisions in the application also avoids unnecessary API requests for records that cannot be classified.

### Keep humans responsible for operational decisions

The model produces an advisory classification. It does not independently authorize or execute logistics operations.

This limits the system's authority and keeps a human responsible for interpreting the result.

### Keep credentials out of source code

The Anthropic API key and browser-demo credentials are configured through environment variables rather than hardcoded into the application.

The local `.env` file is excluded from version control, while `.env.example` provides a template without real credentials.

## Technology Stack

- **Python:** Application logic and workflow implementation.
- **Anthropic Claude API:** Shipment classification.
- **Flask:** Web interface.
- **python-dotenv:** Loading local environment variables.
- **pytest:** Automated testing.

## Getting Started

### Prerequisites

- Python 3.10 or newer; Python 3.12 is recommended.
- Git.
- An Anthropic API key with API access and available usage.
- A terminal and code editor.

A Claude chat subscription does not automatically include API access. API requests may incur charges.

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

A virtual environment isolates this project's Python dependencies from those of other projects on your computer.

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

Replace the placeholder values with your own credentials.

The `DEMO_USERNAME` and `DEMO_PASSWORD` variables are only necessary if the browser interface implements credential-based access.

**Security considerations:**

- Never commit `.env` or other files containing real credentials.
- Keep `.env.example` limited to placeholder values.
- Do not paste API keys into source files or public repositories.
- Use fictional shipment data during development.

## Running the Demo

### Web interface

Start the local Flask application:

```bash
flask --app app run
```

Open the following address in your browser:

http://127.0.0.1:5000

The interface provides two workflows.

**Prepare**

Retrieves and validates a shipment record locally without calling Claude.

**Classify with Claude**

Sends an eligible shipment record to Anthropic for classification. This makes an external API request and may incur charges.

The demonstration is configured to stop after 10 classification attempts per server run. The limit is held in memory and resets when the server restarts.

### Command-line interface

The application also supports running workflows from the terminal.

Prepare a shipment without making an AI request:

```bash
python main.py prepare SHIP-1001
```

Classify an eligible shipment using Claude:

```bash
python main.py classify SHIP-1001
```

Only the classification workflow should call the API, and only after shipment lookup and validation succeed.

## Sample Shipment Records

The local dataset contains fictional records intended to demonstrate different workflow outcomes.

| Tracking number | Scenario | Expected behavior |
|---|---|---|
| `SHIP-1001` | Complete, in-transit shipment | Eligible for Claude classification; intended result: `ON_TRACK`. |
| `SHIP-1002` | Complete shipment with a weather-related delay | Eligible for Claude classification; intended result: `NEEDS_ATTENTION`. |
| `SHIP-1003` | Shipment missing its origin | `NEEDS_MANUAL_REVIEW`; no API call. |
| `SHIP-9999` | Tracking number not found | `NEEDS_MANUAL_REVIEW`; no API call. |

The first two scenarios demonstrate AI classification. The remaining scenarios demonstrate how the application handles records that should not be sent to the model.

The expected AI classifications describe the intended demonstration outcomes, not a guarantee that every model response will be correct.

## Scope and Limitations

The current demonstration:

- Uses fictional shipment records stored locally.
- Does not connect to live carrier tracking systems.
- Uses Claude for advisory classification.
- Routes missing or unsuitable records to manual review.
- Does not modify shipments or execute logistics operations.
- Uses an in-memory limit for classification attempts.

The project focuses on building a controlled AI workflow rather than a complete logistics platform.

Before using a system like this with real customer data or operational workflows, further work would be needed to evaluate security, authentication, privacy, monitoring, error handling, reliability, and usage-limit enforcement.

## Next Steps

Potential areas for further development include:

- Integrating live carrier tracking data.
- Expanding automated tests for validation and classification.
- Evaluating classification accuracy across a broader set of shipment scenarios.
- Improving error handling and monitoring.
- Exploring more robust authentication and persistent usage limits.

The priority is to validate the system's behavior and reliability before expanding its capabilities.