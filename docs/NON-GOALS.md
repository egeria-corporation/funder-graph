# Non-goals

funder-graph does one job: turn IRS Form 990 and 990-PF filings into a clean, queryable
funder-to-recipient grant edge list, and publish it.

Everything below is a thing people will ask for. The answer is no, in advance, so that the answer
is a design decision rather than a mood. Each one has a reason and, where there is one, a pointer
to the right tool.

---

## It will not become a grant discovery product

**No open-opportunity database, no deadlines, no RFP alerts, no application tracking, no matching
engine.** The graph is history. It tells you what a funder has done, which is the single best
predictor of what they will do, but it is not a feed of what is open right now.

Current opportunities are a different data problem with a different refresh cadence and a
different failure mode. [OpenGrants](https://opengrants.io) indexes 139,000+ opportunities and
searches them free, and the optional `OPENGRANTS_API_KEY` layer joins the two. Application
management belongs in `grantdesk`.

## It will not score, rank, or predict

No "fit score", no "likelihood to fund", no propensity model, no lead scoring. The dataset gives
you facts with provenance so you can make a judgment. A model output dressed as a fact is exactly
the thing that makes commercial grant databases untrustworthy, and it is unfalsifiable by
construction: you cannot check a score against a filing.

If you want to build a scoring layer on top of the Parquet, do — that is the point of publishing
it. It will not live in here.

## It will not make eligibility determinations

The graph shows that a foundation funded organizations that look like yours. It does not tell you
that you are eligible, that you should apply, or that they accept unsolicited proposals. Most
private foundations do not. Eligibility signals from the BMF and Publication 78 are `grantcheck`'s
job.

## It will not host program officer names, emails, or phone numbers

Form 990-PF Part XV asks for the name and address of the person to whom applications should be
addressed, and some filings include an individual's name. We do not publish contact-person fields
as a queryable directory. Scraping public filings into a contact list for cold outreach is a
different product with a different ethics posture, and it would poison this project's standing
with the community that made it possible.

## It will not publish grants to named individuals

990-PF filings include scholarship and hardship payments to natural persons. Those rows are
detected, tagged `recipient_type = 'individual'`, and excluded from the default edge view. They
are not part of the funding graph and republishing named individuals with dollar amounts against
their names serves nobody.

## It will not become a general 990 database

We extract grant edges and the header fields needed to give them provenance. We do **not** extract
compensation, balance sheets, functional expense allocation, governance questions, or the other
several thousand fields on the form.

If you want the whole form, the [IRS-Efile-Database
project](https://nonprofit-open-data-collective.github.io/IRS-Efile-Database/) and the
[GivingTuesday 990 Data Commons](https://990data.givingtuesday.org/) already do that, they do it
well, and re-implementing them to own a codebase would be a bad trade.

## It will not ship a web UI beyond the hosted companion

`funders.opengrants.io` is a read-only, server-rendered page per funder. It is not a dashboard, it
has no accounts, no saved searches, no user state, and no login. The moment it needs a session it
has become a different product and belongs in `grantdesk`.

## It will not be a live API you build a business on

The deliverable is a file — versioned Parquet on R2 with no egress cost, queryable directly by
DuckDB, pandas, Polars, Spark, or anything else that reads Parquet. There is a small read-only
JSON endpoint on the hosted site for convenience, and it is explicitly not a supported,
rate-limited, SLA-backed API.

A file you can download and pin is more durable than an API somebody has to keep paying to run.
That is the whole reason this approach beats the incumbents.

## It will not cover non-US or non-990 funding

No UK Charity Commission, no Canadian T3010, no EU foundations, no corporate CSR reports, no
government grants. Form 990 Schedule F reports foreign grantmaking by US filers at the region
level and generally without named recipients, which is not an edge list.

Federal awards are `precedent`'s job (USAspending plus the Federal Audit Clearinghouse).

## It will not require you to run the ETL

The pipeline is in the repo, reproducible, and auditable. Almost nobody should run it. The 60-second
path is a DuckDB query against a hosted URL, and if that stops being true the design has failed.

## It will not add a database dependency

DuckDB reading Parquet over HTTP, or nothing. No Postgres to stand up, no Elasticsearch, no
MongoDB, no docker-compose with six services. Anything that makes the quickstart longer than one
command is out.

---

## How to argue with this list

Open an issue that says which non-goal you want removed, what problem it solves that the current
design does not, and why it belongs in *this* repo rather than a sibling or a downstream project.
That is a real conversation. "It would be cool if" is not.
