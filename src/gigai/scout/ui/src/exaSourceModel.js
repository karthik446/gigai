// uat-bug-033: Exa is an optional extra. The words and rules of the Settings
// toggle, pure so they run under node (test_ui_exa_optional_model.py).

export const EXA_TOGGLE_LABEL = "Also search the open web with Exa (needs an Exa key)";
export const EXA_KEY_COMMAND = "gigai secrets add exa";

// The key hint is shown only when Exa is on and the server said the key is
// not set (`keys` is GET /api/secrets/status' `keys`, or null when it could
// not be read: nothing is claimed then).
export function exaKeyHint({ exaOn, keys }) {
  if (!exaOn || !keys || keys.exa !== false) {
    return null;
  }
  return { text: "Exa key not set", command: EXA_KEY_COMMAND };
}

// `sources` of GET /api/config's config; an old server answering without it
// reads as off.
export function exaIsOn(config) {
  return Boolean(config && config.sources && config.sources.exa);
}
