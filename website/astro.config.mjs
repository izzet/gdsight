import { defineConfig } from 'astro/config';
import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://izzet.github.io',
  base: '/gdsight',
  integrations: [mdx(), sitemap()],
});
