// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
//
// TYPE SCALE (0110-017): Inter, body Light 300; every role has a size, weight, line height, tracking and colour.
// Each text line is a box exactly one line height tall (the CSS model: the half-leading sits above and below
// the glyphs), so the baseline-to-baseline distance between two blocks is their line boxes plus the gap.
//
// SPACING: every vertical gap is a whole multiple of ONE unit `s` = 1.71pt x the spacing scale (sys.inputs,
// 0.7..1.4; the renderer picks it when auto fit is on; below 1.0 the unit shrinks faster: `gap-tighten`).  Bullets 1s, after the title/contact/role line 2s,
// tag rows 3s, after the name 6s, after a section title 8s, between entries 7s, between sections 15s.
// Block spacing collapses to the larger of the two neighbours (Typst's weak spacing).
//
// BODY LINE HEIGHT (0.1.11.5): BELOW 1.0 the scale also tightens the body's line box (bullets, paragraphs, role
// lines), in a straight line from 14.3pt at 1.0 down to `lh-tight` at 0.7, because the gaps alone are too little
// room (0.85 -> 0.70 bought one two-line bullet).  At 1.0 and above the line box is 14.3pt as before: only the gaps
// grow, so a resume at 1.0 or looser prints exactly as it did.  READABILITY FLOOR: `lh-tight` is 11.5pt, the 9.5pt
// body's own single-spaced line (Inter's ascender + descender = 1.21 x the size = 11.49pt): at the floor a line's
// descenders end where the next line's ascenders begin, and they never overlap.  The type SIZE never changes (the
// line box alone gives the room), an entry heading (10.7pt) keeps a line box of at least its own 13pt, and the
// header's lines and the section titles keep theirs.  `fixed_lines` (a page ESTIMATE:
// resume_pdf._estimate) keeps 14.3pt at every scale: the pick and the length rule budget as they did, and the PDF
// never takes more pages than they counted.
//
// AUTO FIT (resume_pdf.fit_scale): the renderer queries <fit-end> below at a few scales and keeps the largest
// that still ends on the fewest pages.  Range: above 1.0 only the gaps grow (~20% of a page's height); below it the
// body's lines tighten too, so 0.7x holds about a quarter more text than 1.0x.  Type sizes and margins never change.
//
// LAYOUT OF FOUR BLOCKS (0.1.11.5 (d); the words are the resume's own, only how they are set changed):
// the Summary is a plain paragraph (no bullet); a degree is ONE line (school, then its degree, the years at the
// right margin; a long degree name a step smaller: `joined`); a project's title and the one line under it are one
// line when they fit; the roles shown by their heading alone have no title of their own when there are fewer than four
// (`untitled`: plain lines after the last role); the Skills are plain comma-separated lines, at most
// `skill-max-lines` (`skills` below).  Everything else is set as before.
//
// PAGE RULES (0110-015): an entry heading and role line stay with the first bullet (sticky), the first bullet
// stays with the second, and the second-to-last stays with the last, so a page break never leaves ONE bullet of
// an entry alone at the bottom of a page or the top of the next.  And no page holds a short block alone (Education,
// the Skills, the roles shown by their heading alone): `short` below.
#let d = json(bytes(sys.inputs.data))
#let scale = float(sys.inputs.at("scale", default: "1.0"))
// A page ESTIMATE (resume_pdf._estimate): the full line height and the plain gap unit at every scale.
#let fixed = d.at("fixed_lines", default: false)
// GAPS BELOW 1.0 (0.1.11.5 PE): below 1.0 the gap unit shrinks `gap-tighten` times as fast as the scale, so 0.85 has
// the gaps 0.70 had (0.70 x 1.71pt) and 0.70 has 0.40 x 1.71pt; the body's line height is the scale's own (12.9pt
// at 0.85), the type never changes, and at 1.0 and above nothing moves.  Why: a real 20-bullet pick was 2 pages
// only at 0.80 with the plain unit; the white space between blocks is what is given up before a point is.  ONE
// number: `gap-tighten = 1.0` is the plain unit again (the gaps in step with the scale, as before this change).
#let gap-tighten = 2.0
#let gap-scale = if scale >= 1.0 or fixed { scale } else { 1.0 - gap-tighten * (1.0 - scale) }
#let s = 1.71pt * gap-scale
#let lh-full = 14.3pt
#let lh-tight = 11.5pt
// The line box of the body's text (`t`'s default, 14.3pt, stays the header's).
#let lh-body = if scale >= 1.0 or fixed { lh-full } else { calc.max(lh-tight, lh-full - (lh-full - lh-tight) * (1.0 - scale) / 0.3) }
#let accent = rgb(d.at("accent", default: "#1F65F5"))
#let ink = rgb("#323336")
#let soft = rgb("#434343")
#let margin-x = 65pt
#let margin-y = 50pt
// Inter's vertical metrics (hhea, all weights): ascender 1984/2048, descender 494/2048.
#let asc = 0.96875
#let desc = 0.2412109375
#let edges(size, lh) = (top-edge: (lh + (asc - desc) * size) / 2, bottom-edge: -(lh - (asc - desc) * size) / 2)
#let t(body, size: 9.5pt, lh: 14.3pt, weight: 300, fill: ink, tracking: 0pt) = text(size: size, weight: weight, fill: fill, tracking: tracking, ..edges(size, lh), body)
#set document(title: d.doc_title, author: if d.name == "" { () } else { d.name })
#set text(font: "Inter", size: 9.5pt, weight: 300, fill: ink, lang: "en", ..edges(9.5pt, 14.3pt))
#set par(leading: 0pt, spacing: 0pt)
#set block(spacing: 0pt)
#set page(paper: "us-letter", margin: (x: margin-x, y: margin-y))
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }
// SKILLS (0.1.11.5 (d)): plain comma-separated lines of real text, in the order given (what the posting asks for
// first), at most `skill-max-lines` lines.  SHRINK BEFORE CUT: the whole list is set at the body's size when it fits
// in those lines, else a step smaller, down to the last of `skill-sizes` (8.3pt, the size the Skills chips had; never
// below it).  Only a list too long even there is cut: the names that fit, from the front, and the rest are not
// printed.  Nothing marks the cut (no "..." and no "and more"): the list claims nothing it does not show.
#let skill-sizes = (9.5pt, 9pt, 8.5pt, 8.3pt)
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
#let bullet(x, keep: false) = block(below: s, sticky: keep, pad(left: 17.8pt, {place(left, dx: -11.8pt, t([•], lh: lh-body)); t(x, lh: lh-body)}))
#let para(x, keep: false) = block(below: 2 * s, sticky: keep, t(x, lh: lh-body, fill: soft))

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
#if d.name != "" { head-name(t(upper(d.name), size: 16.6pt, lh: 16.6pt, weight: 600, tracking: 0.77pt)) }
#if d.title != "" { head-line(d.title) }
// SHRINK BEFORE WRAP (item 16): a contact line too long for the page is set smaller, a step at a time down to the
// last of `contact-sizes` (never below it), in the same 14.3pt line box, so the header is
// as tall as before.  Only a line that is too long even there wraps, at the full size.
#let contact-sizes = (9.5pt, 9pt, 8.5pt, 8pt)
#let contact-line(items) = layout(avail => {
  let made(size) = t(items.map(item).join([ | ]), size: size, fill: soft)
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
  let title = t(if caps { upper(head.text) } else { head.text }, size: 10.7pt, lh: lh-head, weight: 600, tracking: 0.75pt)
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
// NO PAGE OF A SHORT BLOCK ALONE (0.1.11.5 PE, the orphan rule).  A section is SHORT when it has no bullet and no
// paragraph of its own: the Skills, and Education (its degrees are heading lines).  The short sections that END the
// resume are kept with what is before them: the last block before them is sticky, each of them is in one piece (its
// degrees stay together, the Skills' lines are never split), so a page break can only fall before the last two
// bullets (or the last block) of the section in front of them, never between that and Education or the Skills.  The
// same for the roles shown by their heading alone ("Earlier experience"): the last bullet of the role before them
// stays with them.  This is a rule of where a page BREAKS: on a page that holds the blocks anyway nothing moves.
#let short(x) = x.lines.len() == 0 and x.entries.all(e => e.bullets.len() == 0)
#let ends-short(i) = i < d.sections.len() and d.sections.slice(i).all(short)
#for (si, x) in d.sections.enumerate() {
  // What ends this section stays with the next one (a short section that ends the resume).
  let keep = ends-short(si + 1)
  let whole = ends-short(si)
  sec(x.heading)
  for (i, l) in x.lines.enumerate() {
    let last = keep and i == x.lines.len() - 1 and x.tags.len() == 0 and x.entries.len() == 0
    if l.bullet { bullet(l.text, keep: last) } else { para(l.text, keep: last) }
  }
  if x.tags.len() > 0 { block(below: 2 * s, breakable: false, sticky: keep and x.entries.len() == 0, skills(x.tags)) }
  for (ei, e) in x.entries.enumerate() {
    let oneline = e.at("oneline", default: false)
    let untitled = e.at("untitled", default: false)
    let two-lines = e.at("two_lines", default: false)
    let lh-head = calc.max(lh-body, 13pt)
    let last-entry = ei == x.entries.len() - 1
    // The entry's end stays with what follows: the next short section, the next degree of a short section that ends
    // the resume, or the roles shown by their heading alone.
    let stay = if last-entry { keep } else { whole or x.entries.at(ei + 1).bullets.len() == 0 }
    let n = e.bullets.len()
    if oneline {
      // A degree on ONE line: the school as an entry heading, its degree after it in the role line's type (on the
      // heading's baseline: one paragraph), the years at the right margin (`joined`).  A project's title and its
      // second line the same way, with an entry's gap above it.
      block(above: if two-lines { 7 * s } else { 2 * s }, below: 2 * s, breakable: false, sticky: e.heading.len() > 1 or n > 0 or stay, joined(e.heading.at(0), x.caps, lh-head, two-lines))
    } else if not untitled and e.heading.len() > 0 {
      // Sticky heading + role line keep the first bullet with them (a heading is never stranded at a page end).
      block(above: 7 * s, sticky: true, t(if x.caps { upper(e.heading.at(0).text) } else { e.heading.at(0).text }, size: 10.7pt, lh: lh-head, weight: 600, tracking: 0.75pt))
    }
    let under = if untitled { e.heading } else { e.heading.slice(calc.min(1, e.heading.len())) }
    for (i, h) in under.enumerate() {
      let role = if h.dates != "" { grid(columns: (1fr, auto), h.text, h.dates) } else { h.text }
      // A heading's line stays with the entry's first bullet.  The LAST line of an entry with no bullet (the roles
      // shown by their heading alone) may end a page, unless a short section that ends the resume follows it.
      let stick = n > 0 or i < under.len() - 1 or stay
      // An untitled block (roles shown by their heading alone, fewer than four) starts with the gap between entries.
      if untitled and i == 0 { block(above: 7 * s, below: 2 * s, sticky: stick, t(role, lh: lh-body, weight: 400, fill: soft)) } else { block(below: 2 * s, sticky: stick, t(role, lh: lh-body, weight: 400, fill: soft)) }
    }
    for (i, b) in e.bullets.enumerate() { bullet(b, keep: (n >= 2 and (i == 0 or i == n - 2)) or (stay and i == n - 1)) }
  }
}
// Where the content ends, for auto fit (read with `typst query`, never printed).
#context [#metadata((page: here().page(), fill: (here().position().y - margin-y) / (page.height - 2 * margin-y))) <fit-end>]
