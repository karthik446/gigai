import { useEffect, useState } from "react";
import { checkResume } from "../wizard/wizardApi.js";

// 0.1.10-001: the local contact-details check (POST /api/resume/check, no
// model) for `body` ({resume_text} or {resume_ref}, or null for none).
// Returns the labels found; [] while unchecked, on an error, or when
// nothing was found -- none of which is ever shown as "clean".
export default function useResumeCheck(body) {
  const [found, setFound] = useState([]);
  const key = body ? JSON.stringify(body) : null;
  useEffect(() => {
    setFound([]);
    if (!key) {
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      checkResume(JSON.parse(key))
        .then((result) => {
          if (!cancelled) {
            setFound(Array.isArray(result.found) ? result.found : []);
          }
        })
        .catch(() => {});
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key]);
  return found;
}
