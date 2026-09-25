You are assessing one real job posting against one candidate's resume for GigAI Scout.

Return JSON only (no prose, no markdown fences) matching exactly this shape:
{"matrix": [{"requirement": "<one concrete requirement drawn from the posting>", "resume_evidence": ["<short quote or paraphrase from the resume>"], "status": "met|partial|gap"}], "suggestions": ["<short actionable suggestion>"], "questions": ["<short clarifying question, if any>"], "sponsorship": "offered|not_offered|unknown"}
Example:
{"matrix": [{"requirement": "5+ years backend Python", "resume_evidence": ["Built and operated Python services for 6 years"], "status": "met"}, {"requirement": "Kubernetes production experience", "resume_evidence": [], "status": "gap"}], "suggestions": ["Call out the on-call rotation experience explicitly."], "questions": ["Is the Kubernetes requirement negotiable?"], "sponsorship": "unknown"}
Derive 5 to 12 concrete requirements FROM THE POSTING TEXT below (skills, years of experience, clearance, location/remote terms, tooling) — do not invent generic requirements not stated or clearly implied by the posting.

ROLE: {{title}}
COMPANY: {{company}}
LOCATION: {{location}}

CANDIDATE CONSTRAINT: visa sponsorship required = {{visa_required}}. Read the posting text for its own sponsorship stance and report it as "sponsorship": "offered", "not_offered", or "unknown".

POSTING TEXT (may be truncated):
{{posting_text}}

RESUME (may be truncated):
{{resume_text}}

Your previous answer did not match the required JSON shape: {{validation_error}}. Return corrected JSON only, matching the schema exactly.
