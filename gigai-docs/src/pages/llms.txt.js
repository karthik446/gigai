// The public llms.txt (llmstxt.org): a plain-text entry point a user can paste into an agent
// before anything is installed. The text is src/llms.template.txt; {BASE} becomes this build's
// own absolute address (site + DOCS_BASE), so every link goes to a real page of this version and
// never to a /latest/ redirect stub.
import template from '../llms.template.txt?raw';

export function GET({ site }) {
  const base = new URL(import.meta.env.BASE_URL, site).href.replace(/\/*$/, '/');
  return new Response(template.replaceAll('{BASE}', base), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}
