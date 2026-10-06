// 0.1.11.2 (UAT-008): the wizard no longer asks for the company lists, so Settings is where they are edited.
// The two lists are part of the saved preferences (GET/PUT /api/setup); a save sends every other field back as it
// was read, with the two lists replaced.

export function companyListsOf(prefs) {
  const p = prefs || {};
  return { exclude: p.exclude_companies || [], watch: p.watch_companies || [] };
}

export function companyListsBody(prefs, lists) {
  const p = prefs || {};
  return {
    roles: p.roles || [],
    titles_to_avoid: p.titles_to_avoid || [],
    countries: p.countries || [],
    work_mode: p.work_mode,
    city: p.city || null,
    visa_sponsorship_required: Boolean(p.visa_sponsorship_required),
    exclude_companies: lists.exclude,
    watch_companies: lists.watch,
    company_stage_size: p.company_stage_size || null,
    industries_include: p.industries_include || [],
    industries_exclude: p.industries_exclude || [],
    must_have_stack: p.must_have_stack || [],
    dealbreaker_stack: p.dealbreaker_stack || [],
    cadence_days: p.cadence_days,
    budget_usd_per_session: p.budget_usd_per_session,
    max_age_days: p.max_age_days,
    model_target: p.model_target,
  };
}

export function companyListsChanged(prefs, lists) {
  const before = companyListsOf(prefs);
  return JSON.stringify(before) !== JSON.stringify(lists);
}
