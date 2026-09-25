import TagListInput from "../components/TagListInput.jsx";

// Screen 3 -- exclude / always-watch only. The mockup's "start from the
// GigAI company catalog" panel depends on S26 (company directory, after
// 0.1.9) and is omitted here (scope doc F2: "ship screen 3 with exclude/
// watch only, catalog hidden").
export default function CompaniesScreen({ fields, setField, fieldErrors }) {
  const errors = fieldErrors || {};
  return (
    <section className="panel">
      <h2>Companies</h2>
      <p className="muted">Both lists are shared by every profile and drive weekly discovery as well as matching.</p>

      <TagListInput
        id="wz-exclude"
        label="Exclude"
        values={fields.excludeCompanies}
        onChange={(values) => setField("excludeCompanies", values)}
        placeholder="not interested / current employer…"
        error={errors.exclude_companies}
      />

      <TagListInput
        id="wz-watch"
        label="Always watch"
        values={fields.watchCompanies}
        onChange={(values) => setField("watchCompanies", values)}
        placeholder="optional…"
        error={errors.watch_companies}
      />
    </section>
  );
}
