// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
// One leading for every line of body text; a bullet adds `gap` below it, so the space BETWEEN
// bullets is larger than the leading WITHIN one.  Block hierarchy: section heading > entry > role line > bullets.
//
// PAGE RULES (0110-015)
//  1. Keep together: an entry heading and role line stay with the first bullet (sticky), the first bullet stays
//     with the second, and the second-to-last stays with the last, so a page break never leaves ONE bullet of an
//     entry alone at the bottom of a page or the top of the next.
//  2. Fit: the body is measured at the normal spacing.  If it overflows one page by a SMALL amount (at most
//     `small` = 15% of the text height) its vertical spacing is tightened, in order, to x0.88 and then x0.76 of
//     normal (never tighter, never smaller text, margins fixed: a page setting cannot change after measuring),
//     and the first step that fits one page wins.  A larger overflow is not squeezed: it keeps the normal
//     spacing and uses both pages fully.
#let d = json(bytes(sys.inputs.data))
#let ink = rgb("#333333")
#let small = 0.15
#let margin = 40pt
#let levels = (1.0, 0.88, 0.76)
#set document(title: d.doc_title, author: if d.name == "" { () } else { d.name })
#set text(font: "Inter", size: 9.5pt, fill: ink, lang: "en")
#set block(spacing: 0pt)
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }
// Skills: compact wrapping chips (real text, so it is selected and read in order); the .md keeps a "·" line.
#let chip(t) = box(fill: rgb("#f1f4fa"), stroke: 0.5pt + rgb("#cfd8e8"), radius: 3pt, inset: (x: 4.5pt, y: 2.5pt), outset: (y: 0.5pt), text(size: 8.5pt, t))
#let body(scale) = {
  let leading = 0.5em * scale
  let gap = 0.75em * scale  // block edges carry no leading, so this must exceed `leading` by the extra item space
  set par(leading: leading, spacing: 0pt)
  let sec(t) = block(above: 14pt * scale, below: 6pt * scale, sticky: true, text(size: 7.5pt, weight: 600, fill: rgb("#2b6cf0"), tracking: 0.5pt, t))
  let bullet(t, keep: false) = block(above: 0pt, below: gap, sticky: keep, pad(left: 14pt, {place(left, dx: -10pt, [•]); t}))
  let para(t) = block(above: 0pt, below: gap, t)
  if d.name != "" { block(below: 5pt * scale, text(size: 16.5pt, weight: 600, tracking: 0.6pt, d.name)) }
  if d.title != "" { block(above: 0pt, below: 3pt * scale, d.title) }
  if d.contact.len() > 0 { block(above: 0pt, below: 3pt * scale, d.contact.map(item).join([ | ])) }
  for s in d.sections {
    sec(s.heading)
    for l in s.lines { if l.bullet { bullet(l.text) } else { para(l.text) } }
    if s.tags.len() > 0 { block(above: 0pt, below: gap, par(leading: 5.5pt * scale, s.tags.map(chip).join(h(3.5pt)))) }
    for e in s.entries {
      // Sticky heading + role line keep the first bullet with them (a heading is never stranded at a page end).
      if e.heading.len() > 0 { block(above: 11pt * scale, below: 4pt * scale, sticky: true, text(size: 10.7pt, weight: 600, tracking: 0.4pt, e.heading.at(0).text)) }
      for h in e.heading.slice(calc.min(1, e.heading.len())) {
        block(above: 0pt, below: 5pt * scale, sticky: true, if h.dates != "" { grid(columns: (1fr, auto), h.text, h.dates) } else { h.text })
      }
      let n = e.bullets.len()
      for (i, b) in e.bullets.enumerate() { bullet(b, keep: n >= 2 and (i == 0 or i == n - 2)) }
    }
  }
}
#set page(paper: "us-letter", margin: (x: 65pt, y: margin))
#context {
  let avail = page.height - 2 * margin
  let height(scale) = measure(block(width: page.width - 130pt, body(scale))).height
  let chosen = levels.at(0)
  let normal = height(chosen)
  if normal > avail and normal <= avail * (1 + small) {
    for scale in levels.slice(1) { if chosen == levels.at(0) and height(scale) <= avail { chosen = scale } }
  }
  body(chosen)
}
