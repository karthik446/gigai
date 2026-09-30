// 0.1.10-003 1b: the "Resume display" settings model, pure functions (no
// React) so tests/api_e2e/test_ui_resume_display_model.py can run them under
// node. Mirrors src/gigai/scout/resume_display.py: the same kinds, the same
// header the PDF prints (name, title, then the contact items joined by
// " | ", empty items skipped, links shown without the scheme).

export const KINDS = ["location", "work_authorization", "linkedin", "github", "link", "email", "phone"];
export const MAX_CONTACT = 12;

export const KIND_LABELS = {
  location: "Location",
  work_authorization: "Work authorization",
  linkedin: "LinkedIn",
  github: "GitHub",
  link: "Other link",
  email: "Email",
  phone: "Phone",
};

export const KIND_PLACEHOLDERS = {
  location: "City, State",
  work_authorization: "e.g. VISA: H1B",
  linkedin: "linkedin.com/in/you",
  github: "github.com/you",
  link: "example.com",
  email: "you@example.com",
  phone: "+1 555 123 4567",
};

export const PRIVACY_NOTE = "Stays on this machine; added to your PDF locally and never sent to a model.";

const SCHEME = /^[a-z][a-z0-9+.-]*:\/\//i;
const LINK_KINDS = new Set(["linkedin", "github", "link"]);

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function cleanEntries(entries) {
  return (Array.isArray(entries) ? entries : [])
    .filter((entry) => entry && KINDS.includes(entry.kind) && typeof entry.value === "string")
    .map((entry) => ({ kind: entry.kind, value: entry.value }));
}

// How one contact item prints: links without https:// (and no trailing
// slash), everything else as typed.
export function contactText(entry) {
  const value = text(entry && entry.value);
  if (value && LINK_KINDS.has(entry.kind)) {
    return value.replace(SCHEME, "").replace(/\/+$/, "");
  }
  return value;
}

// The draft the form edits: {name, title, contact[], prefilled}. Saved values
// are never overwritten by `suggested`; it only fills what is empty while
// nothing is saved (the server also only sends it then, plus a title).
export function draftFromResponse(response) {
  const body = response || {};
  const saved = body.saved === true;
  const suggested = body.suggested || null;
  let name = text(body.name);
  let title = text(body.title);
  let contact = cleanEntries(body.contact);
  let prefilled = false;
  if (suggested) {
    if (!saved) {
      if (!name && text(suggested.name)) {
        name = text(suggested.name);
        prefilled = true;
      }
      if (contact.length === 0 && cleanEntries(suggested.contact).length) {
        contact = cleanEntries(suggested.contact);
        prefilled = true;
      }
    }
    if (!title && text(suggested.title)) {
      title = text(suggested.title);
      prefilled = true;
    }
  }
  return { name, title, contact, prefilled };
}

// The header exactly as it prints: {name, title, contactLine, items[]}.
export function previewHeader(draft) {
  const items = ((draft && draft.contact) || []).map(contactText).filter(Boolean);
  return {
    name: text(draft && draft.name),
    title: text(draft && draft.title),
    items,
    contactLine: items.join(" | "),
  };
}

export function setEntryValue(contact, index, value) {
  return contact.map((entry, i) => (i === index ? { ...entry, value } : entry));
}

export function setEntryKind(contact, index, kind) {
  return contact.map((entry, i) => (i === index ? { ...entry, kind } : entry));
}

export function removeEntry(contact, index) {
  return contact.filter((_entry, i) => i !== index);
}

// Kinds other than "link" are single-use; the server keeps the first.
export function availableKinds(contact) {
  const used = new Set(contact.map((entry) => entry.kind));
  return KINDS.filter((kind) => kind === "link" || !used.has(kind));
}

export function addEntry(contact, kind) {
  if (contact.length >= MAX_CONTACT || !availableKinds(contact).includes(kind)) {
    return contact;
  }
  return [...contact, { kind, value: "" }];
}

// dir: -1 up, +1 down. Out-of-range moves return the list unchanged.
export function moveEntry(contact, index, dir) {
  const target = index + dir;
  if (index < 0 || index >= contact.length || target < 0 || target >= contact.length) {
    return contact;
  }
  const next = contact.slice();
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

// The PUT body: only this profile's title is sent (the server merges titles
// per profile; an empty one clears it). Empty contact values are dropped so
// what is saved is what the preview shows.
export function buildPutBody(draft, profileId) {
  const body = {
    name: text(draft.name),
    contact: draft.contact.map((entry) => ({ kind: entry.kind, value: text(entry.value) })).filter((entry) => entry.value),
  };
  if (profileId) {
    body.titles = { [profileId]: text(draft.title) };
  }
  return body;
}

// Whether the PDF header has anything beyond a name to print.
export function hasContactLine(response) {
  return draftFromResponse(response).contact.some((entry) => text(entry.value));
}
