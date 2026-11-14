# Security policy

## Supported versions

Security fixes are intended for the latest published version on the default branch.

## Reporting a vulnerability

Do not post access tokens, phone numbers, customer data or exploitable details in a public issue. Use GitHub's private vulnerability reporting feature when it is enabled for the repository, or contact the repository maintainer privately.

## Deployment guidance

- Use a unique dashboard password of at least 16 characters.
- Keep `.env` out of Git and restrict access to it on the host.
- Put the app behind HTTPS before exposing it to the internet or configuring a Meta webhook.
- Configure `META_APP_SECRET` so webhook signatures are verified.
- Restrict access to the host, reverse proxy and persistent database volume.
- Back up the SQLite volume securely and retain only the customer information you need.
