# Release screenshots (`make media`)

Screenshots of Scout, rebuilt every release from a synthetic demo home. No video. Desktop width only.

```sh
brew install tesseract          # once; Linux: apt-get install tesseract-ocr
make media                      # build into build/media (about 100 s; prints timings)
make media-publish              # build, then copy the set to gigai-docs/public/media/ (commit that)
make media-check                # the committed set matches its manifest (no browser needed)
```

The tools are the `media` dependency group in `pyproject.toml` (Playwright, Pillow). It is not a
runtime dependency and not an extra of the published package.

## What it does

| Step | File | What |
|---|---|---|
| Demo data | `persona.py` | Invented companies, postings, persona, answers and stories. Pure data. |
| Demo home | `demo_home.py` | A temporary HOME; the real `gigai setup` / `init` / `scout resume add`; the real sources updater on a transport that serves only the made-up Lever boards; the real Scout server on the fixture model. For the screenshots also: the resumes folder a default install has (`~/Documents/GigAI/resumes`, under the temporary HOME), a master resume made from the profiles' resumes, two lines of it backed by the demo's stories, and four lines added to the hero job's resume. No network, no model. |
| UI | `ui_shots.py` | Playwright, 1280x800, light and dark, one function per shot, `data-testid` selectors. A console error or a failed request fails the build. |
| Terminal | `terminal.py` | A scripted agent transcript around REAL `gigai` commands and their real output. Rendered as an HTML frame by default; `MEDIA_TERMINAL=vhs` uses VHS `Screenshot` (needs `vhs`, `ttyd`, `ffmpeg`). |
| Privacy gate | `privacy_scan.py` | Every image's own text and its pixels (OCR) are checked for the OS user name, home and temp paths, and any email, phone or profile link that is not the persona's. A hit exits non-zero. The build also plants a path in a frame and requires the gate to catch it. |
| Manifest | `manifest.py` | `manifest.json`, the 2 MB budget, and the copy to the docs' fixed paths. |
| Driver | `build.py` | Runs the steps, stops Scout, removes the temporary HOME, prints timings. |

## Privacy

- HOME is a fresh temporary directory before any GigAI code is imported; the build refuses to run
  otherwise. Nothing under your real `~/.gigai` is read.
- The only name in a frame is the persona's. Its email is on `example.test` and its phone is a
  555-01xx number, both reserved for fiction.
- To also check for your own names, set `GIGAI_MEDIA_DENYLIST` to a file with one entry per line
  (or to the entries, comma-separated). Never commit it. A hit prints the entry's number, not its text.

- No frame shows a path under a folder named `home` or `Users`. The demo home itself is
  `<temporary HOME>/demo/home`, so its default resumes folder would show as `~/demo/home/resumes`,
  which the gate reads as a home path (`/home/<name>`), rightly: that failed the 0.1.10.9 release's
  screenshots. The build sets the resumes folder to `~/Documents/GigAI/resumes` instead
  (`demo_home.media_resumes_folder`), which is also what a reader's own Scout shows. The rule is not
  loosened. 0.1.11.3's Generate PDF form also prints the header file's path (`~/demo/home/header.json`,
  a `/home/header.json` hit): the demo's GigAI home is now `<temporary HOME>/.gigai`
  (`demo_home.demo_gigai_home`), so the form shows `~/Documents/GigAI/header.json`. If a new frame shows another path of the demo home, change what the demo shows.

## What the fixture model limits

The fixture model is not a model. Every assessment has the same two requirements (Python, GCP),
it asks one question until that answer is saved, every rank is 60, and the "tailored" resume is
three lines. The screenshots show the product's layout and flow with believable data around
that; they do not show what a real model writes.

With a master resume the fixture is weaker still: its "tailored" resume cites the first line it is
given, which is the `## Summary` heading, and shows none of the master's lines (Picked would be 0).
So the demo adds four master lines to the hero job's resume with the job page's own Add
(`persona.MASTER_ADDED`): Picked says "you added it to this resume" for each, where a model's
tailoring gives the reason each line was picked, and the resume keeps the fixture's odd
`- ## Summary` line. The docs page that embeds these images says so.

## Adding or changing a shot

Add a function and a `Shot(...)` row in `ui_shots.py` (or a `Frame` in `terminal.py`), add its
name to `manifest.py`, run `make media-publish`, look at every image, and commit
`gigai-docs/public/media/`. Wait on a `data-testid`; add one to the UI if the element has none.
