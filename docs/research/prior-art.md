# Prior art, credits, and our contribution plan

This is the most important document in this repository.

funder-graph is not the first attempt to make Form 990 XML usable. It is the first attempt to
publish the *grant edge list* specifically, as a versioned dataset anyone can query without
installing anything — and it is only a six-to-eight-week project instead of a multi-year one
because other people already solved the hardest sub-problem and gave the solution away.

That gift has terms, and the terms are not legal. They are social. This community's endorsement is
the primary distribution channel for this project, and it is far more valuable than owning a
codebase. Burning it to look like we invented something would be an expensive kind of stupid.

The rule everywhere in this repo: **contribute upstream first, credit prominently, never
re-implement what an existing project does well.**

---

## The upstream projects

### Nonprofit Open Data Collective

**https://github.com/Nonprofit-Open-Data-Collective** · overview and issues:
https://nonprofit-open-data-collective.github.io/overview/

An open collaboration of nonprofit researchers, data scientists, and practitioners working on
standards and tooling for nonprofit data. Founded in and around the academic nonprofit-research
community; Jesse Lecy has been the most visible organizer of the concordance work. **VERIFY the
current maintainer list from the repository before naming individuals in public copy** — volunteer
projects change hands and misattributing credit is worse than giving none.

#### IRS E-file Master Concordance File — the single most important upstream asset in this program

- **Docs:** https://nonprofit-open-data-collective.github.io/irs-efile-master-concordance-file/
- **Repo:**
  https://github.com/Nonprofit-Open-Data-Collective/irs-efile-master-concordance-file
- **License:** read the LICENSE file at the commit we pin and record it verbatim in `NOTICE`.

**What it is:** a crosswalk from a stable logical variable name to the correct XPath in each of the
hundreds of 990 XML schema versions. Without it, extracting one field across the corpus means
writing and maintaining hundreds of version-specific XPaths per field, discovering the exceptions
by getting wrong answers, and repeating that for every field. It is the difference between a
tractable project and an intractable one.

**What we use it for:** all field resolution, for both 990-PF Part XV and 990 Schedule I. We pin a
commit SHA, and we stamp that SHA into every published row as `concordance_version`.

**What we do not do:** hand-roll XPaths. Where the concordance is incomplete for our fields, we
carry a local override *and* file the gap upstream, and the override file rejects entries with no
upstream link.

#### IRS-Efile-Database

- **Docs:** https://nonprofit-open-data-collective.github.io/IRS-Efile-Database/

Prior art for normalized relational modeling of the full 990 corpus. Where they have already made a
schema decision that works, we follow it rather than inventing a competing convention. Our scope is
deliberately narrower — grant edges only — and being a well-behaved subset is more useful to the
community than being a rival superset.

### GivingTuesday Data Commons

**https://990data.givingtuesday.org/tool-repository/**

GivingTuesday runs the largest active open effort to make 990 data usable, including a public 990
data mart and the tooling below. They are the most important relationship in this project.

#### form-990-xml-mapper

- **Repo:** https://github.com/Giving-Tuesday/form-990-xml-mapper
- **What it does:** takes any 990 XML schema and produces a CSV of every possible XPath in it.

**What we use it for:** drift detection. For each `returnVersion` in the corpus, we diff the full
XPath inventory against the set our concordance-derived map consumes, restricted to the Part XV and
Schedule I subtrees. Anything present in the schema but unconsumed by us lands in
`build/reports/unmapped-fields.csv`. That report is both our internal QA signal and the raw material
for concordance issues we file upstream.

Running the mapper rather than writing our own XPath enumerator is a deliberate choice. It means our
drift reports are expressed in the same terms the upstream maintainers already use, which makes them
actionable instead of just alarming.

#### form-990-xml-parser

- **Repo:** https://github.com/Giving-Tuesday/form-990-xml-parser
- **What it does:** processes 990 XML into MongoDB.

**Why we do not simply use it as our pipeline:** our output target is columnar Parquet on object
storage optimized for partition-pruned analytic queries over HTTP, and the design is shaped end to
end by that. A MongoDB-targeted parser is the right tool for a different job.

**What we use it for:** it is the reference implementation for repeating-group traversal, schedule
detection, and the handling of the awkward cases — nested groups, optional containers, mixed
cardinality. Where our traversal logic disagrees with theirs, the burden of proof is on us, and any
genuine bug we find in traversal semantics goes to them as an issue with a reproducing filing.

#### Form 990 Variable Dictionary and 990 Data Mart Dictionary

Field semantics and naming conventions. Where GivingTuesday has already named a concept, we use
their name. Gratuitous divergence in vocabulary makes two datasets harder to join and helps nobody.

### open990odl

- **Repo:** https://github.com/990consulting/open990odl
- **Author:** 990 Consulting, LLC

Open data-layer work over 990 filings. Cross-reference for modeling decisions, particularly around
what a "filing" is when amended returns exist.

### propublica990

- **Repo:** https://github.com/Punderthings/propublica990
- **Author:** Punderthings LLC

Tooling for the ProPublica Nonprofit Explorer API. We use it as the reference for well-behaved API
usage — caching, User-Agent, pagination — rather than writing our own client conventions from
scratch. Our ProPublica usage is verification-only and never redistributed.

### NBER Form 990 data

- **https://www.nber.org/research/data/irs-form-990-data**

Long-running academic extracts of 990 financial data. Coverage of earlier years is better than the
e-file corpus in places, and it is the right citation for anyone who needs pre-2019 financials. It
is not an edge list and does not overlap our scope.

### ProPublica Nonprofit Explorer

- **https://projects.propublica.org/nonprofits/**

Not a library, but the reason many people believe 990 data is already accessible. Nonprofit Explorer
is excellent at *filings*: find an organization, see its returns, read the PDF. It is not built to
answer "list every grant this foundation paid, with amounts, sorted" as data. That gap is precisely
where funder-graph sits, and saying so plainly — while giving Nonprofit Explorer full credit for
what it does — is the honest framing.

---

## Our contribution plan

Concrete, scheduled, and assigned. Not "we intend to give back."

### Before v1.0 ships

1. **File every concordance gap we find, individually, with reproducing filings.** Each issue names
   the `returnVersion`, the logical field, the OBJECT_ID of a filing that exhibits it, the XPath we
   believe is correct, and the count of affected filings in our corpus. Volume matters less than
   quality: a gap report with a reproducing document is actionable, a list of complaints is not.
2. **Publish the schema-version coverage matrix.** Every `returnVersion` we encountered, how many
   filings carried it, and whether the concordance covered Part XV and Schedule I for it. Nobody has
   published this. It tells the concordance maintainers exactly where their coverage is thin,
   weighted by real-world filing volume rather than by version count. This is the single most useful
   thing we can hand back, and it is a byproduct of work we have to do anyway.
3. **Contribute a machine-readable, grant-edge-scoped build of the concordance** as a PR upstream —
   the subset of variables that carry grant edges, emitted as JSON, with tests. If upstream does not
   want it in their repo, publish it as a separate small package that credits them clearly, and link
   it from their issue.
4. **Open an issue on form-990-xml-mapper** describing our drift-detection usage. Tool authors
   rarely hear how their tools are actually used downstream, and the report may surface changes that
   make the tool better for everyone.
5. **Reach out before launch, not after.** A short note to the Nonprofit Open Data Collective and
   GivingTuesday describing what we are building, what we are using of theirs, and asking whether
   anything about our approach concerns them — sent *before* the public launch. Costs an afternoon.
   Turns a potential "who are these people" into a potential amplifier. Skipping it is the single
   most likely way this project starts on the wrong foot.

### Ongoing

6. **Publish the `unmatched` table as a first-class dataset artifact.** Every recipient string we
   could not resolve to an EIN, with occurrence counts and candidate counts. This is a shared
   problem for everybody working on 990 data and nobody has published a clean version of it. It is
   also a standing invitation to the community to fix it, and every fix improves the commons rather
   than just our dataset.
7. **Publish the entity-resolution component separately.** The BMF matcher is generally useful to
   anyone joining nonprofit names to EINs, and burying it inside our ETL is hoarding by accident.
   Separate package, Apache 2.0, documented, with the labeled evaluation set.
8. **Monthly upstream reconciliation.** A recurring maintenance task checks every entry in
   `data/overrides/concordance-overrides.toml` against upstream, removes the ones that have landed,
   and bumps any that have gone stale without a response. Overrides accumulating silently is how a
   consumer turns into a fork.
9. **Credit in every surface.** README Credits above the fold, `NOTICE`, the hosted site footer, the
   `llms.txt`, and the dataset `manifest.json`, which carries the concordance commit SHA. Anyone who
   quotes our numbers can see where the mapping came from.

### What we will not do

- Fork the concordance and maintain a competing version.
- Publish a "better parser" positioning that frames GivingTuesday's tools as inadequate.
- Take an upstream fix, ship it in our dataset, and file it upstream later. Upstream first means
  first.
- Present entity resolution as solved. It is not solved, our match tiers say so, and overclaiming
  it would be both dishonest and instantly falsifiable by anyone who checks.

---

## How to add to this file

Any new dependency, adapted technique, or project that materially informed a design decision gets an
entry here and in `NOTICE`, with the author, the link, the license as read from the repo, and what
we actually use it for. "Inspired by" is not a use. Be specific enough that the upstream author
would recognize the description as accurate.
