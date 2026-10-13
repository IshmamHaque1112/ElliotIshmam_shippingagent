"""Command-line entry point for the Shipping Agent demo."""

import argparse
import json
import sys

from anthropic import APIConnectionError, APIError, APIStatusError

from harness import classify_if_ready, prepare_shipment


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a shipment using the local demo dataset.")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_command = commands.add_parser(
        "prepare", help="look up and validate a shipment without calling Claude"
    )
    prepare_command.add_argument("tracking_number")

    classify_command = commands.add_parser(
        "classify", help="look up, validate, and classify a shipment with Claude (API call)"
    )
    classify_command.add_argument("tracking_number")

    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_shipment(args.tracking_number)
    else:
        try:
            result = classify_if_ready(args.tracking_number)
        except RuntimeError as error:
            print(f"Could not classify shipment: {error}", file=sys.stderr)
            return 2
        except APIConnectionError:
            print(
                "Could not reach Anthropic. Check your internet connection and try again.",
                file=sys.stderr,
            )
            return 1
        except APIStatusError as error:
            if error.status_code == 401:
                message = "Anthropic rejected the API key. Check that the local .env key is valid."
            elif error.status_code == 429:
                message = "Anthropic rate or usage limit reached. Check the account limits before retrying."
            elif error.status_code == 402:
                message = "Anthropic reports a billing problem. Check the API account billing settings."
            else:
                message = f"Anthropic returned an API error (HTTP {error.status_code})."

            request_id = getattr(error, "request_id", None)
            if request_id:
                message += f" Request ID: {request_id}."
            print(message, file=sys.stderr)
            return 1
        except APIError:
            print("Anthropic request failed. Check the API configuration and try again.", file=sys.stderr)
            return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
