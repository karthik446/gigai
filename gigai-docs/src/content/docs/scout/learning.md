---
title: Learning pathways
description: The Learning pathways tab in Scout, the two courses from the GigAI learning center, the pathway page built from your resume and stored postings, a course for any role you type, and what is sent where.
---

The **Learning pathways** tab (and `gigai scout learning`) gives you a course for a role: lessons grouped in
modules, each with short subtopics, verified documentation links, diagrams and a hands-on practice page. The lessons
of the courses that come with GigAI are on the public GigAI learning center,
[scout.edgelab.one](https://scout.edgelab.one/learn/); a course you generate or import is plain files served by your
own Scout, and nothing in a course can reach Scout's API.

## The two courses from the GigAI learning center

After you upgrade, the tab shows two courses, labelled **From the GigAI learning center**:

- **MLOps / ML platform engineer**
- **AI forward deployed engineer**

They are generic lessons. They hold no postings and nothing about you: every reader sees the same pages. The lessons
and practice pages live on the learning center; GigAI ships only each course's catalog (its lesson titles and the words
it counts in your postings) and no lesson text.

- **Open on scout.edgelab.one** in the tab (or `gigai scout learning open ROLE_KEY`, which prints the address) opens the
  course's pathway on the learning center in a new browser tab. Your browser makes that request: GigAI makes none and
  nothing of yours is in the address.
- List the courses with `gigai scout learning list`. The line under them, **More pathways on scout.edgelab.one**, links
  to the learning center.
- Reading a lesson needs the network. Your own pathway page, below, is built and read on your computer.

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
- **Every lesson, subtopic and practice link goes to the learning center**, in a new tab. The page's footer says which
  learning center version its catalog was made for; the learning center's own pages say theirs, so if the two differ,
  your catalog is older (`uv tool upgrade gigai`).

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
| Open on scout.edgelab.one, import a folder | Nothing. Opening a course on the learning center is your browser's request. |
| Build my pathway | The lesson titles and your resume lines, contact lines removed. One call. |
| Generate a course | The role you typed, sentences from stored postings, the course text as it is written, and your resume lines (contact lines removed) in the lesson-writing calls. |

Page fetches go to public documentation sites. Your pathway page is served by your own Scout on your computer; the
lessons of the courses from the learning center are served by scout.edgelab.one when you open them. See [Privacy and security](../privacy/#learning-pathways).
