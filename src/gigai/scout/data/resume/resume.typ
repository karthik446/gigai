// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
//
// TYPE SCALE (0110-017): Inter, body Light 300; every role has a size, weight, line height, tracking and colour.
// Each text line is a box exactly one line height tall (the CSS model: the half-leading sits above and below
// the glyphs), so the baseline-to-baseline distance between two blocks is their line boxes plus the gap.
//
// SPACING: every vertical gap is a whole multiple of ONE unit `s` = 1.71pt x the spacing scale (sys.inputs,
// 0.7..1.4; the renderer picks it when auto fit is on).  Bullets 1s, after the title/contact/role line 2s,
// tag rows 3s, after the name 6s, after a section title 8s, between entries 7s, between sections 15s.
// Block spacing collapses to the larger of the two neighbours (Typst's weak spacing).
//
// AUTO FIT (resume_pdf.fit_scale): the renderer queries <fit-end> below at a few scales and keeps the largest
// that still ends on the fewest pages.  Range limit: gaps are ~20% of a page's height, so 0.7x..1.4x moves the
// end by only ~0.2 page -- content up to ~1.1 pages at 1.0x fits one page; a two-page resume reaches a page 2
// >= 80% full only from ~1.6 pages at 1.0x (a 1.5-page one ends near 70%).  Type sizes and margins never change.
//
// PAGE RULES (0110-015): an entry heading and role line stay with the first bullet (sticky), the first bullet
// stays with the second, and the second-to-last stays with the last, so a page break never leaves ONE bullet of
// an entry alone at the bottom of a page or the top of the next.
#let d = json(bytes(sys.inputs.data))
#let scale = float(sys.inputs.at("scale", default: "1.0"))
#let s = 1.71pt * scale
#let accent = rgb(d.at("accent", default: "#1F65F5"))
#let ink = rgb("#323336")
#let soft = rgb("#434343")
#let tag-fill = rgb("#F1F5F7")
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
// Skills: inline chips of real text (selected and read in order); the .md keeps a "·" line.
// COMPACT (0.1.11.3, resume_pdf._render): the same chips with less padding and a smaller row gap, used only when
// the resume would otherwise run past its page limit (a last row of chips alone on an extra page).
#let compact = d.at("compact_tags", default: false)
#let chip(x) = box(fill: tag-fill, inset: if compact { (x: 5pt, y: 1.5pt) } else { (x: 10.7pt, y: 3pt) }, t(x, size: 8.3pt, lh: 12.5pt))
#let sec(x) = block(above: 15 * s, below: 8 * s, sticky: true, t(x, size: 7.1pt, lh: 7.1pt, weight: 600, fill: accent, tracking: 0.71pt))
#let bullet(x, keep: false) = block(below: s, sticky: keep, pad(left: 17.8pt, {place(left, dx: -11.8pt, [•]); x}))
#let para(x) = block(below: 2 * s, t(x, fill: soft))

// 0110-046: an agent's or the CLI's PDF has no header (GigAI stores no name or contact details): a blank block
// as tall as the name line and one contact line keeps the pages the finished PDF (Generate PDF form) will have.
#if d.at("blank_header", default: false) { block(below: 6 * s, height: 16.6pt); block(below: 2 * s, height: 14.3pt) }
#if d.name != "" { block(below: 6 * s, t(upper(d.name), size: 16.6pt, lh: 16.6pt, weight: 600, tracking: 0.77pt)) }
#if d.title != "" { block(below: 2 * s, d.title) }
#if d.contact.len() > 0 { block(below: 2 * s, t(d.contact.map(item).join([ | ]), fill: soft)) }
// 0.1.11.3 item 6: the Generate PDF form's optional "Work authorization" line, a header line of its own.
#if d.at("work_authorization", default: "") != "" { block(below: 2 * s, t(d.work_authorization, fill: soft)) }
#for x in d.sections {
  sec(x.heading)
  for l in x.lines { if l.bullet { bullet(l.text) } else { para(l.text) } }
  if x.tags.len() > 0 {
    block(below: 2 * s, par(leading: if compact { 1.5 * s } else { 3 * s }, text(size: 8.3pt, ..edges(8.3pt, 12.5pt), x.tags.map(chip).join([#h(1.8pt)·#h(1.8pt)]))))
  }
  for e in x.entries {
    // Sticky heading + role line keep the first bullet with them (a heading is never stranded at a page end).
    if e.heading.len() > 0 { block(above: 7 * s, sticky: true, t(if x.caps { upper(e.heading.at(0).text) } else { e.heading.at(0).text }, size: 10.7pt, weight: 600, tracking: 0.75pt)) }
    for h in e.heading.slice(calc.min(1, e.heading.len())) {
      let role = if h.dates != "" { grid(columns: (1fr, auto), h.text, h.dates) } else { h.text }
      block(below: 2 * s, sticky: true, t(role, weight: 400, fill: soft))
    }
    let n = e.bullets.len()
    for (i, b) in e.bullets.enumerate() { bullet(b, keep: n >= 2 and (i == 0 or i == n - 2)) }
  }
}
// Where the content ends, for auto fit (read with `typst query`, never printed).
#context [#metadata((page: here().page(), fill: (here().position().y - margin-y) / (page.height - 2 * margin-y))) <fit-end>]
