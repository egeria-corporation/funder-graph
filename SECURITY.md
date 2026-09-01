# Security Policy

## Reporting a vulnerability

Report security issues privately to **security@egeriacorp.com**, or through GitHub's private vulnerability reporting on this repository. Please do not open a public issue for a security problem.

We aim to acknowledge within two business days and to ship a fix or a mitigation plan within fourteen days for anything exploitable.

## Scope notes for this program

These tools handle organizational data about nonprofits and, in some cases, a consultant's client roster. Two categories deserve particular care:

- **Credential handling.** Tools accept an optional `OPENGRANTS_API_KEY` and, for some repos, federal API keys. Any code path that logs, echoes, or serializes a key into output, telemetry, or an error message is a security bug — report it as one.
- **Local data.** Several tools store client narratives and pipeline data on the user's disk. Anything that transmits that data anywhere without explicit user action is a security bug, regardless of intent.

## What is not a vulnerability

Inaccuracy in public source data (an out-of-date IRS record, a mis-filed 990) is a data quality issue, not a security issue. Open a normal issue for those — they are still worth reporting.
