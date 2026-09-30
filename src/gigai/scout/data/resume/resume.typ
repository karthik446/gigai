// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
// One leading for every line of body text; a bullet adds `gap` below it, so the space BETWEEN
// bullets is larger than the leading WITHIN one.  Block hierarchy: section heading > entry > role line > bullets.
#let d = json(bytes(sys.inputs.data))
#let ink = rgb("#333333")
#let leading = 0.5em
#let gap = 0.75em  // block edges carry no leading, so this must exceed `leading` by the extra item space
#set document(title: d.doc_title, author: if d.name == "" { () } else { d.name })
#set page(paper: "us-letter", margin: (x: 65pt, y: 40pt))
#set text(font: "Inter", size: 9.5pt, fill: ink, lang: "en")
#set par(leading: leading, spacing: 0pt)
#set block(spacing: 0pt)
#let sec(t) = block(above: 14pt, below: 6pt, sticky: true, text(size: 7.5pt, weight: 600, fill: rgb("#2b6cf0"), tracking: 0.5pt, t))
#let bullet(t) = block(above: 0pt, below: gap, pad(left: 14pt, {place(left, dx: -10pt, [•]); t}))
#let para(t) = block(above: 0pt, below: gap, t)
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }
#if d.name != "" { block(below: 5pt, text(size: 16.5pt, weight: 600, tracking: 0.6pt, d.name)) }
#if d.title != "" { block(above: 0pt, below: 3pt, d.title) }
#if d.contact.len() > 0 { block(above: 0pt, below: 3pt, d.contact.map(item).join([ | ])) }
#for s in d.sections {
  sec(s.heading)
  for l in s.lines { if l.bullet { bullet(l.text) } else { para(l.text) } }
  for e in s.entries {
    // Sticky heading + role line keep the first bullet with them (a heading is never stranded at a page end).
    if e.heading.len() > 0 { block(above: 11pt, below: 4pt, sticky: true, text(size: 10.7pt, weight: 600, tracking: 0.4pt, e.heading.at(0).text)) }
    for h in e.heading.slice(calc.min(1, e.heading.len())) {
      block(above: 0pt, below: 5pt, sticky: true, if h.dates != "" { grid(columns: (1fr, auto), h.text, h.dates) } else { h.text })
    }
    for b in e.bullets { bullet(b) }
  }
}
