// @ts-check
// GigAI public docs. One build = one docs version: the release workflow builds each tag with
// DOCS_VERSION=<x.y.z> DOCS_BASE=/gigai/<x.y.z>/ and scripts/publish-version.mjs files it on gh-pages.
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightOpenAPI, { openAPISidebarGroups } from 'starlight-openapi';
import mermaid from 'astro-mermaid';

export default defineConfig({
  site: process.env.DOCS_SITE ?? 'https://karthik446.github.io',
  base: process.env.DOCS_BASE ?? '/',
  trailingSlash: 'always',
  integrations: [
    mermaid({ autoTheme: true }),
    starlight({
      title: 'GigAI',
      description: 'A local, user-controlled agent runtime, and the Gigs built on it.',
      favicon: '/favicon.svg',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/karthik446/gigai' }],
      editLink: { baseUrl: 'https://github.com/karthik446/gigai/edit/main/gigai-docs/' },
      components: { SocialIcons: './src/components/HeaderExtras.astro' },
      plugins: [
        // Scout's API (0110-007). Moves with Scout's section when Scout gets its own repo.
        starlightOpenAPI([
          { base: 'scout/reference/api', schema: './src/content/generated/scout-openapi.json', sidebar: { label: 'API reference' } },
        ]),
      ],
      sidebar: [
        'roadmap',
        { label: 'GigAI', items: ['index', 'install', 'concepts/architecture', 'concepts/gigs', 'concepts/runs', 'concepts/model-targets', 'concepts/review', 'agents', 'development', 'reference/cli'] },
        // One group per Gig: its own folder, its own sidebar entry, nothing else to move.
        {
          label: 'Scout',
          badge: { text: 'Gig', variant: 'tip' },
          items: ['scout', 'scout/first-10-minutes', 'scout/quickstart', 'scout/privacy', 'scout/resume', 'scout/numbers', 'scout/tokens', 'scout/accuracy-0-1-11', 'scout/sources', 'scout/configuration', 'scout/agents', 'scout/agents/start', 'scout/limitations', 'scout/roadmap', 'scout/reference/cli', ...openAPISidebarGroups],
        },
        { label: 'Project', items: ['changelog'] },
      ],
    }),
  ],
});
