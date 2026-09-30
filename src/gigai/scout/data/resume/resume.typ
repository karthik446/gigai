// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
#let d = json(bytes(sys.inputs.data))
#let ink = rgb("#333333")
#set document(title: d.doc_title, author: if d.name == "" { () } else { d.name })
#set page(paper: "us-letter", margin: (x: 65pt, y: 40pt))
#set text(font: "Inter", size: 9.5pt, fill: ink, lang: "en")
#set par(leading: 0.62em, spacing: 0.5em)
#let sec(t) = block(above: 13pt, below: 6pt, sticky: true, text(size: 7.5pt, weight: 600, fill: rgb("#2b6cf0"), tracking: 0.5pt, t))
#let bullet(t) = block(above: 0pt, below: 2.5pt, pad(left: 14pt, {place(left, dx: -10pt, [•]); t}))
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }
#if d.name != "" { block(below: 4pt, text(size: 16.5pt, weight: 600, tracking: 0.6pt, d.name)) }
#if d.title != "" { block(above: 0pt, below: 2pt, d.title) }
#if d.contact.len() > 0 { block(above: 0pt, below: 2pt, d.contact.map(item).join([ | ])) }
#for s in d.sections {
  sec(s.heading)
  for l in s.lines { bullet(l) }
  for e in s.entries {
    block(above: 9pt, below: 3pt, breakable: false, {
      if e.heading.len() > 0 { text(size: 10.7pt, weight: 600, tracking: 0.4pt, e.heading.at(0)) }
      for h in e.heading.slice(calc.min(1, e.heading.len())) { linebreak(); h }
      if e.bullets.len() > 0 { v(3pt); bullet(e.bullets.at(0)) }
    })
    for b in e.bullets.slice(calc.min(1, e.bullets.len())) { bullet(b) }
  }
}
