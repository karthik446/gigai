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

## The search index

At the end of an update Scout also builds and keeps current a small local index of every stored posting,
`<home>/cache/scout/search.sqlite`. **Search all jobs** (`gigai scout jobs search`, `GET /api/search`) reads
it. It is a cache like the company files: delete it and the search falls back to reading every company
file, which gives the same rows, slower (the output says so). A search never builds it. After upgrading to
0.1.11.7, run `gigai scout sources update` once to build it.

## Find jobs reads that store

Find jobs does not check the boards itself. A search
takes your watchlist companies' stored postings, applies your titles
([which titles match](#which-titles-match)), the
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

## Background updates

While Scout is open it keeps the store current by itself. The strip at the top
of the Jobs page says what it did, for example `updated 12 min ago · next
check at 13:00`, and never starts anything itself: an update starts from the
**Update sources** and **Full refresh** buttons, or from the automatic check.

- **Automatic checks.** Eight times a day on weekdays (03:00, then every two
  hours from 07:00 to 19:00) and twice on weekend days (09:00 and 18:00), in
  your local time, Scout checks the busy company boards, and each quiet board
  about twice a day. A check runs at a moderate pace on purpose (a few
  requests a second, backing off when a site asks it to), so it takes ten to
  fifteen minutes and then idles. If Scout was closed, one catch-up check runs
  when you open it. It waits for your first **Update sources**, and steps
  aside while an update you started is running. **Update sources** itself runs
  at full speed.
- **Title tags.** Each stored title gets a level and a function, first from
  built-in rules (no model). Titles the rules cannot place are sent to the
  model Scout is set up with, only for the roles your profiles search for.
  The strip counts them, for example `Function: 4,400 by rules, 600 by model,
  150 waiting for the model, 3,000 not tagged by a model (background tagging of
  the rest is off)`; only titles a model will actually tag are called waiting. If the model cannot be reached the strip says so in one line and
  Scout tries again later; searching keeps working.
- **Descriptions.** `Descriptions checked for 6,100 postings, 2,900 not yet`
  is how many stored postings have their description text indexed on this
  machine, which is what a keyword search can look in.

All of this is switched in **Settings > Background updates**; see
[Configuration](../configuration/#background-updates).

## Which titles match

A posting is in a profile's list when its title is one of the profile's
titles **as a whole role**, not when the title's words merely appear:

- Every word of your title must be a word of the posting's title. Case,
  punctuation and small words (of, the, and, for, in) do not matter, and
  "Senior" or "Sr." in your title is not required.
- The words must stand together. A word between them is the kind of the
  same role and is kept: for the title "Staff Engineer", "Staff Software
  Engineer", "Senior Staff Engineer" and "Staff Payments Engineer" match.
  A short fixed list of words there makes it another role, and the posting
  is not listed: Training, Trainer, Sales, Presales, Support, Solutions,
  Customer, Field, Application, Program, Recruiting, Recruiter, Coordinator
  and Intern. "Staff Training Engineer", "Staff Sales Engineer" and "Staff
  Application Engineer" do not match "Staff Engineer". A title of yours that
  says the word matches it: add "Staff Training Engineer" to get those.
- A title is read in parts, cut at commas, brackets, slashes, colons and
  dashes. What comes after the role, in the same part or a later one, names
  the team and is free: "Staff Engineer - Payments", "Staff Engineer
  (Backend)", "Staff Software Engineer, ML Training Infrastructure" all match.
  The inverted form matches too: "Director, Engineering" and "Engineering
  Director" for "Director of Engineering". There the part before the comma
  holds only the role's own words, level words and discipline words, so
  "Staff Accountant, Engineering" is no "Staff Engineer".
- Another role's word next to it is another job: "Staff Engineering Manager",
  "Director, Software Engineering", "Software Engineering Trainer" and "Staff
  Engineer in Training" do not match "Staff Engineer" or "Software Engineer".
  In front of an engineering title the listed words do the same ("Sales
  Engineering Manager"), except Application and Program ("Application
  Security Engineer" is a "Security Engineer"), and unless your own title
  says that word.

A title tag adds postings for one kind of title only. A title of yours that
is plain software engineering and has no qualifier ("Director of
Engineering", "Staff Software Engineer") also lists a posting whose tag has
the same level and the software function, though a word of yours is missing
from its title: "Dir. of Engineering", "Director, Software Development". Every
other title matches by its words alone:

- A title of another function ("Forward Deployed Engineer", "Staff AI
  Engineer", "Product Manager"). Those functions hold many different jobs, so
  "Forward Deployed Engineer" does not list "Solutions Architect" or
  "Implementation Consultant".
- A title with a qualifier after a comma, a colon, a bracket or a dash ("Staff
  Software Engineer, AI Platform", "Software Engineer (Backend)"). The
  qualifier's words must be in the posting's title, so it does not list every
  "Staff Software Engineer". A level there is no qualifier ("Software
  Engineer, Staff").

**Titles to avoid** (setup, and each profile's settings) take postings out of
that profile's list. An entry is a word or a phrase; a posting whose title
holds it is not listed at all. Whole words only, any case, no word stemming:
"Trainer" does not remove "Training", and "intern" does not remove
"Internal". A phrase must appear with its words in a row ("application
engineer" removes "Staff Application Engineer, Salesforce"). The whole title
is read, so avoiding "training" also removes "Staff Software Engineer, ML
Training Infrastructure". Another profile without that entry still lists the
posting. The list is matched again right after you change either setting.

Whether a posting sponsors visas is never a filter. A posting that says it
does not sponsor stays in the list with a "No sponsorship" label, also when
your settings say you need a visa.

## Keywords

The run dialog has an optional **Keywords** field (up to 20, each up to 100
characters). A keyword is an exact phrase, with no word stemming. Keywords
narrow a search and never widen it: of the postings your titles and filters
already matched, one is kept when its stored description mentions any one of
the keywords. A posting whose description is not stored on this machine
cannot be checked, so it is kept, and the run says how many: `Keywords: rust,
payments · text not checked for 12 postings`. If no description text is
indexed yet, the keywords are ignored and the run says why. From the API,
send them as `keywords` on `POST /api/run`.

## The starter snapshot

So that a new install does not start from an empty store, **Update sources**
first tries to download a shared starter snapshot and then checks the boards
as usual. The strip shows `Starter data: as of <date>`. If no snapshot is
published, or the machine is offline, the update simply carries on without
it.

- It holds metadata only: company boards, posting titles, locations and
  dates, the title tags, and the validators that let a board answer "not
  changed". It never holds posting descriptions.
- The download is a plain public request. Nothing about you, your resume or
  your searches is sent.
- Each file is checked against the SHA-256 in the snapshot's manifest before
  it is used; a file that does not match is not used.
- A board this machine has checked itself is never overwritten by a snapshot.
- Turn it off under **Settings > Background updates** ("Use the shared
  starter snapshot"), or run `gigai scout snapshot status` to see what is in
  use.

## Where the data comes from

Company and title data comes from the public job boards of each company
(Greenhouse, Lever, Ashby), read with plain public requests. The starter
snapshot redistributes only the metadata listed above, never an employer's
description text. A company that wants its board left out of the snapshot can
ask by opening an issue on the
[GigAI repository](https://github.com/karthik446/gigai/issues); boards on the
removal list are left out of the next snapshot and removed from stores that
only had them from a snapshot.
