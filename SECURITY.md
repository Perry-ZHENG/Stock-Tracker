# Security Policy

## Supported Versions

This project is a portfolio and research-oriented stock monitoring agent. The
latest version on the default branch is the only supported version.

## Reporting A Vulnerability

If you find a security issue, please do not open a public issue with exploit
details or sensitive information.

Report privately by contacting the repository owner. Include:

- A short description of the issue.
- Steps to reproduce.
- Impact and affected component.
- Any relevant logs with secrets removed.

## Secret Handling

Do not commit real API keys, Telegram tokens, broker credentials, account
identifiers, or other secrets. Use environment variables for all credentials.

If a secret is accidentally committed, rotate or revoke it immediately and
remove it from Git history before publishing the repository.

## Safety Scope

This project is not an automated trading system. Security reports related to
brokerage execution, account access, or fund movement should assume those flows
are out of scope unless such integrations are explicitly added later.
