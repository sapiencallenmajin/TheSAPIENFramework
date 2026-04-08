import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';

export async function GET(context: APIContext) {
  const posts = (await getCollection('blog'))
    .filter(post => !post.data.draft)
    .sort((a, b) => b.data.date.getTime() - a.data.date.getTime());

  return rss({
    title: 'SAPIEN Framework Blog',
    description: 'Updates, research notes, and announcements from the SAPIEN Behavioral Safety Framework.',
    site: context.site ?? 'https://sapienframework.org',
    items: posts.map(post => ({
      title: post.data.title,
      pubDate: post.data.date,
      description: post.data.description,
      link: `/blog/${post.id.replace(/\.md$/, '')}/`,
    })),
  });
}
