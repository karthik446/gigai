// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
//
// TYPE SCALE (0110-017; 0.3.5 gigai-lc3: the look of Standard Resume's "Design" view): Inter, body Regular 400 in
// near-black `ink`, tracked -0.08pt (0.3.6, gigai-3jj: `t`'s default and `set text`; Regular is wider than the Light
// 300 of 0.3.4, and untracked it pushed resumes that fit two pages on 0.3.4 onto a third); every role has a size,
// weight, line height, tracking and colour.  Each text line is a box exactly one line height tall (the CSS model: the
// half-leading sits above and below the glyphs), so the baseline-to-baseline distance between two blocks is their line
// boxes plus the gap.
//
// SPACING: every vertical gap is a whole multiple of ONE unit `s` = 1.71pt x `gap-scale` (the spacing scale,
// sys.inputs, 0.7..1.4; the renderer picks it when auto fit is on).  Bullets 1s, after the title/contact/role line 2s,
// tag rows 3s, after the name 6s, after a section title 8s, between entries 7s (12pt at 1.0: the last bullet's baseline
// to the next employer's is two line pitches), between sections 15s.  Below 1.0 the unit shrinks `gap-tighten` (2.0)
// times as fast as the scale: at 0.7 `gap-scale` is 0.4, so a bullet gap is 0.68pt, an entry gap 4.8pt and a section
// gap 10.3pt; the hierarchy holds at every scale because every gap is the same unit's multiple.  (0.3.5 had a constant
// 4pt bullet gap and the scale's own unit below 1.0; with the Regular body and the 12.5pt line floor that cost some
// 200pt at 0.7 and a third page for two-page resumes, so 0.3.6 restores 0.3.4's gaps.)
// Block spacing collapses to the larger of the two neighbours (Typst's weak spacing).
//
// BODY LINE HEIGHT (0.1.11.5): BELOW 1.0 the scale also tightens the body's line box (bullets, paragraphs, role
// lines), in a straight line from 14.3pt at 1.0 down to `lh-tight` at 0.7 (12.4pt at 0.8), because the gaps alone are
// too little room.  At 1.0 and above the line box is 14.3pt: only the gaps grow.  READABILITY FLOOR: `lh-tight` is
// 11.5pt, the 9.5pt body's own single-spaced line (Inter's ascender + descender = 1.21 x the size = 11.49pt): at the
// floor a line's descenders end where the next line's ascenders begin, and they never overlap (0.3.6 restores it from
// 0.3.4; 0.3.5's 12.5pt floor was one of the costs above).  The type SIZE never changes (the line box alone gives the
// room), an entry heading (10.7pt) keeps a line box of at least its own 13pt, and the header's lines and the section
// titles keep theirs.  `fixed_lines` (a page ESTIMATE: resume_pdf._estimate) keeps 14.3pt and the untightened unit
// (`gap-scale` = the scale) at every scale: the pick and the length rule budget with a line box and gaps never smaller
// than the PDF's, and the PDF never takes more pages than they counted.
//
// AUTO FIT (resume_pdf.fit_scale): the renderer queries <fit-end> below at a few scales and keeps the largest that
// still ends on the fewest pages; with a page limit no scale reaches, it takes the tightest, 0.7x (0.3.6).  Range:
// above 1.0 only the gaps grow (~20% of a page's height); below it the gaps shrink faster and the body's lines tighten
// too, so 0.7x holds about a quarter more text than 1.0x.  Type sizes and margins never change.
//
// THE LOOK (0.3.5, gigai-lc3): the header is the name in letterspaced capitals, the headline, ONE contact line, and
// the Summary paragraph straight under them with NO title of its own when the Summary is the first section
// (`summary-heading`; a Summary elsewhere keeps its title); an employer is letterspaced capitals (10.7pt, 600, 0.04em)
// and its role line is "Title | Jun 2022 - Present" on one line, the dates after the pipe, never at the right margin
// (every role line: an entry's roles, several roles at one employer, the roles shown by their heading alone); a
// degree's and a project's years stay at the right margin (`joined`).
//
// LAYOUT OF FOUR BLOCKS (0.1.11.5 (d); the words are the resume's own, only how they are set changed):
// the Summary is a plain paragraph (no bullet); a degree is ONE line (school, then its degree, the years at the
// right margin; a long degree name a step smaller: `joined`); a project's title and the one line under it are one
// line when they fit; the roles shown by their heading alone have no title of their own when there are fewer than four
// (`untitled`: plain lines after the last role); the Skills are plain comma-separated lines, at most
// `skill-max-lines` (`skills` below).  Everything else is set as before.
//
// PAGE RULES (0.1.11.5 PB5: an entry SPLITS across pages; before, a role of two or three bullets was one piece and
// left a wide empty band at the foot of the page it did not fit on):
// - an entry's heading (the employer and its role lines; a project's title and its line of technologies) stays with
//   its FIRST bullet: a heading is never the last line of a page;
// - a bullet of up to four printed lines is one piece (`bullet`): a page never breaks inside it;
// - a section title stays with the first block under it (`sec`);
// - a SHORT BLOCK (Education's degrees; the roles shown by their heading alone) never leaves one of its lines alone
//   on a page: its title stays with its first line, its last two lines stay together (`short` below for the degrees,
//   the `stick` clause of an entry's lines for the roles);
// - otherwise a page breaks wherever it ends: between any two bullets, between two entries, between two of the
//   Skills' lines, between a short block's other lines.  Nothing is kept with what follows it across a section, so a
//   page before the last is never left short by more than ONE of the pieces above (0.3.3, gigai-0rs; the orphan
//   rule of 0.1.11.5 is gone: see the comment above the section loop).
#let d = json(bytes(sys.inputs.data))
#let scale = float(sys.inputs.at("scale", default: "1.0"))
// A page ESTIMATE (resume_pdf._estimate): the full line height and the untightened gap unit at every scale.
#let fixed = d.at("fixed_lines", default: false)
// The gap unit (see SPACING above): below 1.0 it shrinks `gap-tighten` times as fast as the scale.
#let gap-tighten = 2.0
#let gap-scale = if scale >= 1.0 or fixed { scale } else { 1.0 - gap-tighten * (1.0 - scale) }
#let s = 1.71pt * gap-scale
#let lh-full = 14.3pt
#let lh-tight = 11.5pt
// The line box of the body's text (`t`'s default, 14.3pt, stays the header's).
#let lh-body = if scale >= 1.0 or fixed { lh-full } else { calc.max(lh-tight, lh-full - (lh-full - lh-tight) * (1.0 - scale) / 0.3) }
#let accent = rgb(d.at("accent", default: "#1F65F5"))
#let ink = rgb("#1C1C1E")
#let soft = rgb("#2B2B2E")
#let margin-x = 65pt
#let margin-y = 50pt
// Inter's vertical metrics (hhea, all weights): ascender 1984/2048, descender 494/2048.
#let asc = 0.96875
#let desc = 0.2412109375
#let edges(size, lh) = (top-edge: (lh + (asc - desc) * size) / 2, bottom-edge: -(lh - (asc - desc) * size) / 2)
#let t(body, size: 9.5pt, lh: 14.3pt, weight: 400, fill: ink, tracking: -0.08pt) = text(size: size, weight: weight, fill: fill, tracking: tracking, ..edges(size, lh), body)
// A PLACEHOLDER HEADER (0.1.11.5 PH; the job page's preview only, never a PDF: resume_pdf._render): "Your Name" and
// one contact line stand where the person's own header will print, the same lines in the same boxes, in a lighter grey.
#let placeholder = d.at("placeholder", default: false)
#let faint = rgb("#A6A8AD")
#set document(title: d.doc_title, author: if d.name == "" or placeholder { () } else { d.name })
#set text(font: "Inter", size: 9.5pt, weight: 400, fill: ink, lang: "en", tracking: -0.08pt, ..edges(9.5pt, 14.3pt))
#set par(leading: 0pt, spacing: 0pt)
#set block(spacing: 0pt)
#set page(paper: "us-letter", margin: (x: margin-x, y: margin-y))
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }
// SKILLS (0.1.11.5 (d)): plain comma-separated lines of real text, in the order given (what the posting asks for
// first), at most `skill-max-lines` lines.  SHRINK BEFORE CUT: the whole list is set at the body's size when it fits
// in those lines, else a step smaller, down to the last of `skill-sizes` (8pt, as small as `contact-sizes` goes; never
// below it).  The Regular 400 body is wider than the Light 300 it was: at 8.3pt a real list lost two skills (measured
// on 0.3.5, before the body's -0.08pt tracking).  Only a
// list too long even at 8pt is cut: the names that fit, from the front, and the rest are not
// printed.  Nothing marks the cut (no "..." and no "and more"): the list claims nothing it does not show.
#let skill-sizes = (9.5pt, 9pt, 8.5pt, 8.3pt, 8pt)
#let skill-max-lines = 4
#let skills(names) = layout(avail => {
  let made(n, size) = block(width: avail.width, t(names.slice(0, n).join(", "), size: size, lh: lh-body, fill: soft))
  let fits(n, size) = measure(made(n, size)).height <= skill-max-lines * lh-body + 0.01pt
  let whole = skill-sizes.find(size => fits(names.len(), size))
  if whole != none { made(names.len(), whole) } else {
    let size = skill-sizes.last()
    let (lo, hi) = (1, names.len())
    while lo < hi {
      let mid = calc.quo(lo + hi + 1, 2)
      if fits(mid, size) { lo = mid } else { hi = mid - 1 }
    }
    made(lo, size)
  }
})
#let sec(x) = block(above: 15 * s, below: 8 * s, sticky: true, t(x, size: 7.1pt, lh: 7.1pt, weight: 600, fill: accent, tracking: 0.71pt))
// A BULLET IS ONE PIECE: a page never breaks inside a bullet of up to `whole-lines` printed lines (a resume point is
// two or three).  A longer one may break, with two lines or more on each side (Typst's own rule), as it always could:
// kept whole, a point of six lines left up to six lines empty at the foot of a page, and a resume of such points took
// a page more.  The lines are measured, at the width the bullet prints at, and only for a bullet of more than
// `whole-chars` characters (a shorter one is not five lines of ordinary text; measuring every bullet made a layout half
// as slow again, and a pick asks for many).
#let whole-lines = 4
#let whole-chars = 330
#let bullet(x) = {
  let body = pad(left: 17.8pt, {place(left, dx: -11.8pt, t([•], lh: lh-body)); t(x, lh: lh-body)})
  if x.len() <= whole-chars { block(below: s, breakable: false, body) } else {
    context block(below: s, breakable: measure(block(width: page.width - 2 * margin-x, body)).height > whole-lines * lh-body + 0.01pt, body)
  }
}
#let para(x) = block(below: 2 * s, t(x, lh: lh-body, fill: soft))

// THE HEADER (0.1.11.3 item 15) is COMPACT: the name line, the optional title line, then ONE contact line whose items
// are joined with " | " (the renderer orders them: location, work authorization, links, email, phone) and which wraps
// only when it is too long for the page.  `head-line` is the ONE definition of a header line under the name: the
// printed lines and the blank block below are made of it.
#let head-name(body) = block(below: 6 * s, body)
#let head-line(body) = block(below: 2 * s, body)
// 0110-046: an agent's or the CLI's PDF has no header (GigAI stores no name or contact details): a blank block as
// tall as the name line and `blank_lines` header lines.  The headerless PDF keeps one; the page ESTIMATE of a pick
// (resume_pdf.HEADER_RESERVE_LINES) keeps the header at its largest, so the finished PDF stays on the pick's pages.
#if d.at("blank_header", default: false) {
  head-name(block(height: 16.6pt))
  for _ in range(int(d.at("blank_lines", default: 1))) { head-line(block(height: 14.3pt)) }
}
#if d.name != "" { head-name(t(upper(d.name), size: 16.6pt, lh: 16.6pt, weight: 600, tracking: 0.77pt, fill: if placeholder { faint } else { ink })) }
#if d.title != "" { head-line(if placeholder { text(fill: faint, d.title) } else { d.title }) }
// SHRINK BEFORE WRAP (item 16): a contact line too long for the page is set smaller, a step at a time down to the
// last of `contact-sizes` (never below it), in the same 14.3pt line box, so the header is
// as tall as before.  Only a line that is too long even there wraps, at the full size.
#let contact-sizes = (9.5pt, 9pt, 8.5pt, 8pt)
#let contact-line(items) = layout(avail => {
  let made(size) = t(items.map(item).join([ | ]), size: size, fill: if placeholder { faint } else { soft })
  let fits = contact-sizes.find(size => measure(made(size)).width <= avail.width)
  made(if fits == none { contact-sizes.first() } else { fits })
})
#if d.contact.len() > 0 { head-line(contact-line(d.contact)) }
// ONE LINE FOR A HEADING AND ITS SECOND LINE (0.1.11.5 PE; `oneline` entries: a degree, and a project whose title has
// a second line, its technologies).  SHRINK BEFORE WRAP: the second part (never the school or the title) is set a step
// smaller at a time, down to the last of `join-sizes` (8.3pt, the Skills' smallest; never below it), until the whole
// line fits with `join-gap` before the years at the right margin.  When no size fits:
// - a project (`two_lines`) prints as it did before: its title, then the second line under it;
// - a degree keeps the school and its degree on one line when they fit without the years (the years then go to the
//   right margin of a second line: the last resort); a degree too long even for that wraps at the full size, the
//   years at the end of its last line.
#let join-sizes = (9.5pt, 9pt, 8.5pt, 8.3pt)
#let join-gap = 10pt
#let joined(head, caps, lh-head, two-lines) = layout(avail => {
  let title = t(if caps { upper(head.text) } else { head.text }, size: 10.7pt, lh: lh-head, weight: 600, tracking: 0.43pt)
  let after(body, size) = text(size: size, weight: 400, fill: soft, ..edges(10.7pt, lh-head), body)
  let left(size) = { title; if head.detail != "" { after([ | ] + head.detail, size) } }
  let dates = after(head.dates, 9.5pt)
  let room = avail.width - if head.dates != "" { measure(dates).width + join-gap } else { 0pt }
  let fits = join-sizes.find(size => measure(left(size)).width <= room)
  if fits != none { left(fits); if head.dates != "" { h(1fr); dates } } else if two-lines {
    block(title)
    block(t(if head.dates != "" { grid(columns: (1fr, auto), head.detail, head.dates) } else { head.detail }, lh: lh-body, weight: 400, fill: soft))
  } else {
    let alone = join-sizes.find(size => measure(left(size)).width <= avail.width)
    if alone != none and head.dates != "" { block(left(alone)); block(width: 100%, align(right, dates)) } else { left(join-sizes.first()); if head.dates != "" { h(1fr); dates } }
  }
})
// A SHORT SECTION BREAKS LIKE ANY OTHER (0.3.3, gigai-0rs; it replaces the orphan rule of 0.1.11.5 PE).  Before, what
// ended the resume with no bullet in it (the Skills, Education, the roles shown by their heading alone) was ONE piece
// with the last bullet before it, so that the last page always held a bullet.  That chain (some 215pt at 0.7) moved
// whole to a new page when it missed the room left by a few points, and left a third of the page before it empty.
// Now a bullet is never kept with what follows it and the Skills' lines break where the page ends (the title stays
// with the first line: `sec`; Typst keeps two lines on each side).  A short block (`short`: Education, whose entries
// are all degrees; the roles shown by their heading alone get the same by the `stick` clause of their lines) keeps only what spares a line alone on a page: its title
// with its first line, its last two lines together; so two degrees are one piece, three may split one and two.  The
// cost of a page break is then one line or two, never a block, and a short block is never kept with what follows its
// section.  So the last page may hold only the Skills' last lines and Education, Education alone, or two of the
// earlier roles: that is what a page before it filled to within one piece costs, and the smaller cost.
// THE SUMMARY IS PART OF THE HEADER (0.3.5, gigai-lc3): when the first section is the Summary, its paragraph prints
// straight under the contact line with no title (the reference look); the next section's title brings its own gap.
#let summary-heading = "SUMMARY"
#for (si, x) in d.sections.enumerate() {
  let short = x.entries.len() > 0 and x.entries.all(e => e.at("oneline", default: false) and e.bullets.len() == 0)
  if not (si == 0 and x.heading == summary-heading) { sec(x.heading) }
  for l in x.lines { if l.bullet { bullet(l.text) } else { para(l.text) } }
  if x.tags.len() > 0 { block(below: 2 * s, skills(x.tags)) }
  for (ei, e) in x.entries.enumerate() {
    let oneline = e.at("oneline", default: false)
    let untitled = e.at("untitled", default: false)
    let two-lines = e.at("two_lines", default: false)
    let lh-head = calc.max(lh-body, 13pt)
    // Education's second-to-last degree stays with the last (never a degree alone on a page); any other may end a page.
    let stay = short and ei == x.entries.len() - 2
    let n = e.bullets.len()
    if oneline {
      // A degree on ONE line: the school as an entry heading, its degree after it in the role line's type (on the
      // heading's baseline: one paragraph), the years at the right margin (`joined`).  A project's title and its
      // second line the same way, with an entry's gap above it.
      block(above: if two-lines { 7 * s } else { 2 * s }, below: 2 * s, breakable: false, sticky: e.heading.len() > 1 or n > 0 or stay, joined(e.heading.at(0), x.caps, lh-head, two-lines))
    } else if not untitled and e.heading.len() > 0 {
      // Sticky heading + role line keep the first bullet with them (a heading is never stranded at a page end).
      block(above: 7 * s, sticky: true, t(if x.caps { upper(e.heading.at(0).text) } else { e.heading.at(0).text }, size: 10.7pt, lh: lh-head, weight: 600, tracking: 0.43pt))
    }
    let under = if untitled { e.heading } else { e.heading.slice(calc.min(1, e.heading.len())) }
    for (i, h) in under.enumerate() {
      // "Title | Jun 2022 - Present": the dates after a pipe on the role's own line (0.3.5; before, at the right margin).
      let role = if h.dates == "" { h.text } else if h.text == "" { h.dates } else { h.text + " | " + h.dates }
      // A heading's line stays with the entry's first bullet, a degree's further lines with its next degree, and the
      // second-to-last line of an entry with no bullet (the roles shown by their heading alone) with the last.
      let stick = n > 0 or stay or (oneline and i < under.len() - 1) or (n == 0 and i == under.len() - 2)
      // An untitled block (roles shown by their heading alone, fewer than four) starts with the gap between entries.
      if untitled and i == 0 { block(above: 7 * s, below: 2 * s, sticky: stick, t(role, lh: lh-body, weight: 400, fill: soft)) } else { block(below: 2 * s, sticky: stick, t(role, lh: lh-body, weight: 400, fill: soft)) }
    }
    for b in e.bullets { bullet(b) }
  }
}
// Where the content ends, for auto fit (read with `typst query`, never printed).
#context [#metadata((page: here().page(), fill: (here().position().y - margin-y) / (page.height - 2 * margin-y))) <fit-end>]
