# Security and data handling

Use development accounts and synthetic data for local review. Never commit credentials, message contents, customer records, operational runbooks or local database files. Supply service credentials through your own local environment and review integrations before connecting them.

If a credential is exposed, revoke or rotate it at the issuing service. Removing a file or a Git commit does not revoke access. Do not post suspected secrets in public issues.

This is a development portfolio snapshot, not a security certification or a production deployment. Report a vulnerability through GitHub private vulnerability reporting if available; avoid publishing exploit details or private data.
