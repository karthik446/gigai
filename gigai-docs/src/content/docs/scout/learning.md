---
title: Learning pathways
description: The Learning pathways tab in Scout, the two courses that ship with GigAI, the pathway page built from your resume and stored postings, a course for any role you type, and what is sent where.
---

The **Learning pathways** tab (and `gigai scout learning`) gives you a course for a role: lessons grouped in
modules, each with short subtopics, verified documentation links, diagrams and a hands-on practice page. A course is
plain files served by your own Scout; nothing in a course can reach Scout's API.

## The two courses that ship with GigAI

After you upgrade, the tab shows two courses:

- **MLOps / ML platform engineer**
- **AI forward deployed engineer**

They are generic lessons. They hold no postings and nothing about you: every install gets the same files. Open one
from the tab, or list them with `gigai scout learning list`. More courses that ship with GigAI are coming.

## Build my pathway

A shipped course becomes yours with one button, **Build my pathway** (`gigai scout learning pathway ROLE_KEY`). It
writes one extra page on your own computer:

- **One model call**, about 32k tokens and about 90 seconds. It sends the course's lesson titles and your master
  resume lines, contact lines removed, to your model. Scout asks first and shows the estimate; the answer is no
  unless you approve (`--yes` in the CLI).
- **Your resume is always the reference.** There is no switch to leave it out. The call marks each lesson and
  subtopic as **known**, **some** or **new**, with the resume line that shows it. Every answer is checked in code
  before it is written. With no master resume nothing is sent, and the page marks nothing as known.
- **The percentages are counted in code**, not by a model, from the postings already stored on your computer: how
  often they ask for each thing the course names.
- **The page is your own file.** It lives in your project's learning folder and is never shipped or uploaded.
  A build that fails writes nothing; the page that was there stays as it was.

When your resume changes, the tab says **your resume changed since** you built the page, and the button reads
**Rebuild my pathway**. The same goes for a GigAI update that changed the course or for changed stored postings.
Rebuilding is another one-call build, and it asks first again.

## A course for any role you type

:::caution
A course uses about 1 million tokens. On an entry-level plan (about $20 a month, such as Claude Pro or ChatGPT Plus) that can use up most or all of your weekly limit; use a higher plan, or one of the courses that come with GigAI.
:::

Type any role (Product manager, Director of AI enablement, ...) in the tab, or run
`gigai scout learning generate "ROLE"`. GigAI builds a course from public documentation and from what your own stored
postings ask for. Lessons are teachable methods and tools, not traits.

- **It shows an estimate and asks first.** Real runs of the two shipped courses took 43 and 48 model calls, about
  0.86 and 1.0 million tokens, and about 2 to 2.5 hours of background work. Plan for roughly 43 to 48 calls and about
  1 million tokens; see [Token usage](../tokens/#learning-pathways).
- **It stops by itself** after 120 model calls or 1,500 page fetches. What was written is kept.
- **It runs in the background** with plain progress. You can cancel it (`gigai scout learning cancel ID`) and
  resume it later: only what is missing costs calls and fetches. One course runs at a time per project.
- **It marks what you know** from your master resume, contact lines removed.

## Import a folder

`gigai scout learning import FOLDER --role "ROLE"` adds a course folder somebody already generated. It is checked
whole first (links, anchors, size caps) and makes no model call and no network request.

## What is sent where

| Action | Sent to your model |
| --- | --- |
| Open a shipped course, import a folder | Nothing. |
| Build my pathway | The lesson titles and your resume lines, contact lines removed. One call. |
| Generate a course | The role you typed, sentences from stored postings, the course text as it is written, and your resume lines (contact lines removed) in the lesson-writing calls. |

Page fetches go to public documentation sites. The shipped courses and your pathway page are served by your own
Scout on your computer. See [Privacy and security](../privacy/#learning-pathways).
