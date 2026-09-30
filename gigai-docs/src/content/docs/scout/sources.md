---
title: Update sources
description: How Scout checks company job boards, and how find jobs uses the result.
---

Checking the company boards is its own step, separate from searching:

```bash
gigai scout sources update          # check every company board on the watchlist
gigai scout sources status --json   # the last update, and whether the stored postings are current
```

`gigai scout sources update` (the UI's **Update sources**, `POST
/api/sources/update`) makes one polite conditional request per board on your
watchlist: the catalog companies your setup admits plus the ones you added.
An unchanged board costs one small request and changes nothing. It prints
what it found, e.g. `120 companies with new postings: 412 new, 95 changed,
230 removed`. An update stops at its time budget (20 minutes by default;
`--budget-seconds 0` for no limit) and the next one continues with the boards
it has not reached yet, so the first update over the full catalog takes a few
passes and later ones a single short pass.

What it learns is stored on this machine only, one plain JSON file per
company:

```
<home>/cache/scout/companies/<ats>:<slug>.json
```

Each file lists the company's postings with when each was first seen, last
seen, changed or removed. It is a cache, not a record: it is safe to delete
(one file or the whole folder), and the next update rebuilds it from the
board responses already cached under `<home>/cache/scout/ats-boards/`.
Nothing in it leaves the machine.

## Find jobs reads that store

Find jobs does not check the boards itself. A search
takes your watchlist companies' stored postings, applies your titles, the
publication window, the country rule and your work-mode preference, ranks
every posting that passes, and fully assesses the top-ranked ones (the run's
"Full assessments" setting) in the background, with no board request, so it takes seconds, not minutes. **Assess all new** on the finished run assesses the rest. Run **Update
sources** first, and again whenever you want fresh postings:

- nothing stored yet: the search says `Run Update sources` instead of
  fetching;
- the last update is more than a day old: the search still uses what is
  stored and says the postings are out of date.

The one exception: a company Exa discovers during a search that is not in
the store yet is fetched then and added to the store, so its postings can be
assessed in that same run. At most 20 such companies are fetched per search.
The limit counts companies, not requests: one company can take more than one
request (on Greenhouse, one for the list and one per posting whose title
matches yours). Companies over the limit wait for the next **Update
sources**.
