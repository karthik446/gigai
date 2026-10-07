// A ONE-PAGE COVER LETTER in the resume's template family (0.1.11.4 C2; cover_letter.render_letter_pdf).
// Data arrives as JSON via sys.inputs (never string-templated, so no markup injection).
//
// THE FAMILY: the page, the margins, Inter, the colours, the line-box model and the compact header are resume.typ's,
// definition for definition (tests/behaviors/scout_find_jobs/test_cover_letter_pdf.py holds the two files to that).
// The body is the letter's own: paragraphs of plain text, a little larger than a resume line.
//
// SPACING: one unit `s` = 1.71pt x the spacing scale (sys.inputs).  A letter starts at 1.0; when it does not fit one
// page the renderer goes down a step at a time to 0.7, the same floor as the resume: the gaps between paragraphs and
// the line height get tighter, the type size and the margins never change.  A letter that needs a second page even
// there is too long, and the renderer says so.
#let d = json(bytes(sys.inputs.data))
#let scale = float(sys.inputs.at("scale", default: "1.0"))
#let s = 1.71pt * scale
#let ink = rgb("#323336")
#let soft = rgb("#434343")
#let margin-x = 65pt
#let margin-y = 50pt
// Inter's vertical metrics (hhea, all weights): ascender 1984/2048, descender 494/2048.
#let asc = 0.96875
#let desc = 0.2412109375
#let edges(size, lh) = (top-edge: (lh + (asc - desc) * size) / 2, bottom-edge: -(lh - (asc - desc) * size) / 2)
#let t(body, size: 9.5pt, lh: 14.3pt, weight: 300, fill: ink, tracking: 0pt) = text(size: size, weight: weight, fill: fill, tracking: tracking, ..edges(size, lh), body)
// The resume's header, line for line (resume.typ): its placeholder form is the resume PREVIEW's only; a letter's data never sets it.
#let placeholder = d.at("placeholder", default: false)
#let faint = rgb("#A6A8AD")
#set document(title: d.doc_title, author: if d.name == "" or placeholder { () } else { d.name })
#set text(font: "Inter", size: 9.5pt, weight: 300, fill: ink, lang: "en", ..edges(9.5pt, 14.3pt))
#set par(leading: 0pt, spacing: 0pt)
#set block(spacing: 0pt)
#set page(paper: "us-letter", margin: (x: margin-x, y: margin-y))
#let item(c) = if c.url != none { link(c.url, c.text) } else { c.text }

// THE HEADER is the resume's compact one: the name line, then ONE contact line (resume.typ, 0.1.11.3 items 15/16).
#let head-name(body) = block(below: 6 * s, body)
#let head-line(body) = block(below: 2 * s, body)
// A letter made without a header keeps a blank block of the header's height, so adding the header later moves nothing.
#if d.at("blank_header", default: false) {
  head-name(block(height: 16.6pt))
  for _ in range(int(d.at("blank_lines", default: 1))) { head-line(block(height: 14.3pt)) }
}
#if d.name != "" { head-name(t(upper(d.name), size: 16.6pt, lh: 16.6pt, weight: 600, tracking: 0.77pt, fill: if placeholder { faint } else { ink })) }
#let contact-sizes = (9.5pt, 9pt, 8.5pt, 8pt)
#let contact-line(items) = layout(avail => {
  let made(size) = t(items.map(item).join([ | ]), size: size, fill: if placeholder { faint } else { soft })
  let fits = contact-sizes.find(size => measure(made(size)).width <= avail.width)
  made(if fits == none { contact-sizes.first() } else { fits })
})
#if d.contact.len() > 0 { head-line(contact-line(d.contact)) }

// THE BODY: 10.5pt, line height 1.5 at scale 1.0 down to 1.38 at 0.7 (the readable floor).
#let body-size = 10.5pt
#let body-lh = body-size * (1.1 + 0.4 * scale)
#let gap = 7 * s
#let lines(b) = b.lines.map(l => t(l, size: body-size, lh: body-lh)).join(linebreak())
#v(14 * s)
#for b in d.blocks {
  if b.bullet {
    block(below: gap / 2, pad(left: 17.8pt, {place(left, dx: -11.8pt, t([•], size: body-size, lh: body-lh)); lines(b)}))
  } else {
    block(below: gap, lines(b))
  }
}
// Where the content ends, for the one-page fit (read with `typst query`, never printed).
#context [#metadata((page: here().page(), fill: (here().position().y - margin-y) / (page.height - 2 * margin-y))) <fit-end>]
