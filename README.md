# WhatsApp Campaign Studio

A small, self-hosted campaign manager for the **official Meta WhatsApp Business Platform Cloud API**. It provides contact import, audience groups, template-based campaigns, webhook delivery updates and opt-out handling.

> This is an early MVP. It is not a WhatsApp Web bot, does not automate a personal WhatsApp account, and does not bypass Meta's messaging rules. Meta's platform charges may apply to messages sent in live mode.

## What it includes

- Password-protected dashboard (HTTP Basic authentication).
- CSV contact import with optional custom columns.
- Explicit opt-in and consent-source recording; phone numbers must be supplied in international E.164 format.
- Contact groups; campaigns are scoped to one selected group.
- Approved-template lookup from the connected WhatsApp Business Account.
- Positional template body-parameter mapping from contact fields/CSV columns.
- Dry-run mode enabled by default.
- Sequential campaign processing with a configurable delay.
- Delivery/read/failure updates through Meta webhooks.
- Opt-out keywords (`STOP`, `STOP ALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, `REMOVE`) received through webhooks are applied to matching contacts.
- Docker deployment with a persistent SQLite volume.
- MIT licence and GitHub Actions tests.

## Requirements

- Docker Engine and Docker Compose plugin (recommended), or Python 3.12+.
- A Meta developer app and a WhatsApp Business Account with Cloud API access for live messaging.
- A public HTTPS endpoint for Meta webhooks when live webhook updates are needed.

## Quick start with Docker

```bash
git clone https://github.com/razorjetinz/whatsapp-campaign-studio.git
cd whatsapp-campaign-studio
cp .env.example .env
```

Generate strong secrets (run separately):

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Put the first value in `APP_PASSWORD` and the second in `WEBHOOK_VERIFY_TOKEN` in `.env`. Keep the values private. Then start:

```bash
docker compose up --build -d
docker compose logs -f
```

Open **http://localhost:8080**. The browser will ask for the dashboard username and password (`APP_USERNAME` and `APP_PASSWORD` from `.env`). The port is bound to localhost by default; use a properly configured HTTPS reverse proxy if you need remote access or a public webhook endpoint.

To stop the app:

```bash
docker compose down
```

The database remains in the Docker volume `campaign_data`. To remove all stored contacts and campaigns as well, remove that volume explicitly with `docker compose down -v`.

## Run without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with strong values, then:

```bash
set -a
. ./.env
set +a
python run.py
```

The SQLite database defaults to `./data/campaign-studio.db` when using the sample configuration only after changing `DATABASE_PATH` from `/data/campaign-studio.db` to a writable local path.

## Connect Meta WhatsApp Cloud API

1. Create/configure a Meta developer app and add the WhatsApp product/use case.
2. Complete the WhatsApp Business Account and phone-number setup in Meta's developer/business tools.
3. Obtain a suitable long-lived/system-user access token with the WhatsApp messaging and business-management permissions required by your app.
4. Copy the values into `.env`:

   ```dotenv
   META_ACCESS_TOKEN=your_private_access_token
   META_PHONE_NUMBER_ID=your_phone_number_id
   META_WABA_ID=your_whatsapp_business_account_id
   META_APP_SECRET=your_meta_app_secret
   GRAPH_API_VERSION=v27.0
   ```

5. Keep `DRY_RUN=true` and restart the app. Open **Templates** and choose **Refresh from Meta**. The app lists templates Meta reports with `APPROVED` status.
6. In the Meta app's Webhooks settings, configure the WhatsApp Business Account webhook callback URL as `https://YOUR_DOMAIN/webhook`, use the exact `WEBHOOK_VERIFY_TOKEN` value from `.env`, and subscribe to the `messages` field. The webhook endpoint also consumes message status events delivered under that field.
7. Confirm the webhook is reachable over HTTPS. Set `META_APP_SECRET`; the app validates `X-Hub-Signature-256` when this value is configured. Live mode refuses to start without the app secret.
8. Test your contact import, groups and campaign in dry-run mode. Check the generated recipient list and consent records.
9. Only when you are ready for real delivery, set `DRY_RUN=false`, ensure all four Meta credential values are present, and restart the service. The app verifies template approval before campaign creation and launch.

Meta's API, permissions, onboarding screens, API versions and pricing can change. Use the official documentation as the authority:

- [WhatsApp Business Platform overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/)
- [Cloud API getting started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started)
- [Messages and templates](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates)
- [Webhook setup](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks)
- [Template management API](https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/message_templates/)

## Import contacts

Upload a UTF-8 CSV with at least a `phone` column. Recommended columns:

```csv
name,phone,opted_in,consent_source,company
Asha,+919876543210,yes,Website signup,Acme
Rahul,+919876543211,no,,Northwind
```

- Use `opted_in=yes` only when the person has explicitly agreed to receive WhatsApp messages from your business.
- Record a meaningful `consent_source` for opted-in contacts. Keep your separate evidence of consent as required by your business/legal obligations; this app stores only the stated source and timestamp.
- Numbers must include `+` and country code, e.g. `+919876543210`. The app deliberately does not guess a country code.
- Extra CSV columns are stored as contact custom fields and can be used in template body parameter mapping (for example, `name,company`).
- Rows with `opted_in=no` may be imported for record management, but are never campaign recipients.

## Create a campaign

1. Create a group and add/import contacts into it.
2. Open **Templates** and refresh approved templates (live connection required for the list).
3. Create a campaign and enter the exact approved template name and language code.
4. If the approved template body contains positional placeholders such as `{{1}}` and `{{2}}`, provide matching contact field names in order (for example `name,company`).
5. Review the eligible recipient total and create the draft.
6. Launch only after reviewing the recipient list and confirming the campaign. Dry-run mode simulates delivery without contacting WhatsApp.

Live sending uses only template messages. This MVP does not provide free-form bulk messaging, automatic consent collection, scheduling, campaign pause controls, multi-user roles, media uploads or an inbound support inbox.

## Important operational and compliance notes

- Send only to people who have opted in to receive the relevant type of WhatsApp messages from your business. Store and honour opt-outs promptly.
- Use message templates with the correct category and approval status. Follow WhatsApp's customer-service window, template and messaging policies; this project intentionally keeps campaign sends template-based.
- The app checks contact consent immediately before each recipient send. If an opt-out webhook arrives, the contact is excluded from future sends.
- Meta can change rate limits, quality thresholds, template requirements and charges. The default inter-message delay is a conservative pacing setting, not a promise of compliance with all Meta throughput limits.
- The dashboard is designed for a single trusted operator on a self-hosted instance. Do not expose it publicly without HTTPS, firewall/reverse-proxy restrictions, secure backups and operational monitoring.
- The webhook is publicly reachable by design; set `META_APP_SECRET` so webhook signatures are validated.
- Do not upload purchased, scraped or otherwise non-consented contact lists.

## Development and tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

No Meta credentials are required for the test suite. Tests use temporary SQLite databases.

## Environment variables

| Variable | Purpose | Required |
|---|---|---|
| `APP_USERNAME` | Dashboard username | No (defaults to `admin`) |
| `APP_PASSWORD` | Dashboard password, minimum 16 characters | Yes |
| `WEBHOOK_VERIFY_TOKEN` | Meta webhook verification token | Yes |
| `META_ACCESS_TOKEN` | Meta API access token | Live mode |
| `META_PHONE_NUMBER_ID` | WhatsApp sender phone-number ID | Live mode |
| `META_WABA_ID` | WhatsApp Business Account ID | Live mode |
| `META_APP_SECRET` | Verify webhook signatures | Live mode |
| `GRAPH_API_VERSION` | Graph API version | No (defaults to `v27.0`) |
| `DRY_RUN` | Simulate sends when `true` | No (defaults to `true`) |
| `SEND_DELAY_SECONDS` | Delay between recipient sends | No (defaults to `1.5`) |
| `DATABASE_PATH` | SQLite database file | No |
| `PORT` | Web server port | No (defaults to `8080`) |

## License

MIT. See [LICENSE](LICENSE).

## Trademark note

WhatsApp and Meta are trademarks of their respective owners. This independent project is not affiliated with, endorsed by or sponsored by Meta Platforms, Inc. or WhatsApp LLC.
