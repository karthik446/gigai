// 0.1.11.2 (after-release #2 and #10): the address a job page is opened with, read the way the store keeps it.
// Pure functions, no React. normalizeJobAddress mirrors find_jobs/contracts.normalize_url: the scheme and host
// lower-cased, a default port dropped, the path without its trailing slashes (before the query too), the utm_*
// and tracking parameters dropped, the rest sorted, no fragment.

const TRACKING_KEYS = new Set(["fbclid", "gclid", "gh_src", "lever-source", "ref", "source"]);

export function normalizeJobAddress(address) {
  let url;
  try {
    url = new URL(String(address));
  } catch {
    return null;
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || !url.hostname) {
    return null;
  }
  const pairs = [];
  new URLSearchParams(url.search).forEach((value, key) => {
    const lower = key.toLowerCase();
    if (!lower.startsWith("utm_") && !TRACKING_KEYS.has(lower)) {
      pairs.push([key, value]);
    }
  });
  pairs.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const query = new URLSearchParams(pairs).toString();
  // URL drops a default port itself; hostname is lower-cased by URL.
  return `${url.protocol}//${url.host.replace(/\.$/, "")}${path}${query ? `?${query}` : ""}`;
}

// The id a job page looks up: the address as given when a known job has it, else a known job whose address
// normalizes to the same one, else the normalized address (a stored posting is keyed by it), else as given.
export function resolveJobId(address, knownIds) {
  const known = knownIds instanceof Set ? knownIds : new Set(knownIds || []);
  if (!address || known.has(address)) {
    return address;
  }
  const normal = normalizeJobAddress(address);
  if (!normal) {
    return address;
  }
  if (known.has(normal)) {
    return normal;
  }
  for (const id of known) {
    if (normalizeJobAddress(id) === normal) {
      return id;
    }
  }
  return normal;
}

// The stored on-demand assessment for an address, or null: the item's job identity or normalized address is the
// address (normalized both ways). Its id is what #/assessments/<id> opens.
export function onDemandItemFor(quickItems, address) {
  const normal = normalizeJobAddress(address);
  if (!normal) {
    return null;
  }
  const same = (value) => Boolean(value) && (value === address || normalizeJobAddress(value) === normal);
  const item = (quickItems || []).find((entry) => entry && entry.job && (same(entry.job.job_identity) || same(entry.job.normalized_url)));
  if (!item) {
    return null;
  }
  return { id: item.job.job_identity || item.job.normalized_url, item };
}
