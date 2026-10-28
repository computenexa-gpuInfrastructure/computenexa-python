# Security policy

## Reporting a vulnerability

Please report security issues privately to **support@computenexa.com**, with "Security" in the subject.
Don't open a public GitHub issue for a security problem.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to acknowledge reports
within 3 business days and will keep you updated until it's resolved. We're grateful for responsible disclosure
and are happy to credit you once a fix is released, if you'd like.

This covers this SDK (the `computenexa` package) and the ComputeNexa API it talks to
(`https://api.computenexa.com`).

## Supported versions

Security fixes go into the latest release. Upgrade with:

```bash
pip install --upgrade computenexa
```

## If an API key leaks

API keys (`cnxk_…`) act as the person who created them and can spend your team's credits. If one may have been
exposed (committed to a repository, pasted in a chat, shared in a log), revoke it right away in the dashboard under
**API keys** and create a new one. Revoking takes effect immediately.
