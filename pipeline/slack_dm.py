r"""Send a Slack DM through the Francis bot — used to mirror the routines' summary emails.

  slack_dm.cmd --file <message.md> [--user <SlackUserID>]

The message file is Slack mrkdwn (*bold*, _italic_, <url|text>, "• " bullets). Long messages
are split into several posts. The recipient defaults to config.json "slack_report_user_id"
(Cory Hester). Uses the bot token in secrets/slack.json (needs chat:write + im:write).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="UTF-8 text file with the Slack message (mrkdwn)")
    ap.add_argument("--user", help="Slack user ID (default: config slack_report_user_id)")
    a = ap.parse_args()

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    user = a.user or config.get("slack_report_user_id")
    if not user:
        sys.exit("ERROR: no recipient — set slack_report_user_id in config.json")
    secrets = ROOT / "secrets" / "slack.json"
    if not secrets.exists():
        sys.exit("ERROR: secrets/slack.json not found (Francis isn't set up)")
    token = json.loads(secrets.read_text(encoding="utf-8")).get("bot_token", "")
    if not token.startswith("xoxb-"):
        sys.exit("ERROR: no Slack bot token in secrets/slack.json")

    text = Path(a.file).read_text(encoding="utf-8").strip()
    if not text:
        sys.exit("ERROR: message file is empty")

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    client = WebClient(token=token)
    try:
        channel = client.conversations_open(users=user)["channel"]["id"]
        # Slack caps a message at ~40k chars but renders best under ~3.5k: split on blank lines.
        parts, cur = [], ""
        for block in text.split("\n\n"):
            if cur and len(cur) + len(block) + 2 > 3500:
                parts.append(cur)
                cur = block
            else:
                cur = f"{cur}\n\n{block}" if cur else block
        parts.append(cur)
        first = client.chat_postMessage(channel=channel, text=parts[0], unfurl_links=False)
        for p in parts[1:]:  # keep the rest in a thread under the first post
            client.chat_postMessage(channel=channel, text=p, thread_ts=first["ts"], unfurl_links=False)
    except SlackApiError as e:
        sys.exit(f"ERROR: Slack said {e.response.get('error')}")
    print(f"Sent Slack DM to {user} ({len(parts)} message(s))")


if __name__ == "__main__":
    main()
