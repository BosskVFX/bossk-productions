// Monthly auto-draft newsletter generator.
// Triggered by Vercel Cron (see vercel.json) on the 1st of each month.
// Researches recent AI/VFX industry news via Claude's web search tool,
// pulls in Bossk's most recent published case study for context,
// drafts a newsletter, saves it to the sheet as a DRAFT (never sends),
// and pings Slack so a human can review + send from /admin.html.

export default async function handler(req, res) {
  // Allow Vercel Cron (GET) or a manual trigger with the right secret.
  const CRON_SECRET = process.env.CRON_SECRET;
  if (CRON_SECRET) {
    const auth = req.headers['authorization'];
    if (auth !== `Bearer ${CRON_SECRET}`) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
  }

  const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
  const SLACK_WEBHOOK = process.env.SLACK_WEBHOOK;
  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxEXavIf3HNYjEY16k28O3MnJv7WQLRwlFaPUDnMZKcsjnWrp3mjSydsU4mPA_UsbtP/exec';

  if (!ANTHROPIC_KEY) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY not set' });
  }

  // Pull the most recent published case study for context, so the
  // newsletter can naturally reference recent Bossk work if relevant.
  let recentWork = '';
  try {
    const csRes = await fetch(SHEET_URL + '?sheet=CaseStudy', { redirect: 'follow' });
    const csData = await csRes.json();
    const published = csData.filter(r => {
      const pub = r.Published || r['Published '];
      return pub === true || pub === 'TRUE' || String(pub).toUpperCase() === 'TRUE';
    });
    if (published.length) {
      const latest = published[published.length - 1];
      recentWork = `Most recent published Bossk Productions case study — Title: "${latest.Title}". Subtitle: "${latest.Subtitle}". Result: "${latest.Result}"`;
    }
  } catch (e) {
    console.error('Fetch recent work error:', e.message);
  }

  const systemPrompt = `You are the newsletter writer for Bossk Productions, an Emmy Award-winning AI production studio in Los Angeles. You write "The Bossk Brief" — a monthly newsletter for subscribers interested in AI video production, VFX, and filmmaking.

Use the web_search tool to find 2-3 genuinely interesting, recent (last 30-45 days) developments in AI video generation, VFX tooling, or AI filmmaking. Prioritize real news: new model releases, notable AI-made films/ads, industry shifts. Skip generic listicles or SEO spam.

${recentWork ? `Also weave in a brief, natural mention of this recent Bossk Productions work if it fits: ${recentWork}` : ''}

Write with authority and a bit of edge — this is a working studio's newsletter, not a corporate blog. Short paragraphs. No fluff, no "in today's fast-paced world" openers.

Output ONLY valid JSON, no markdown fences, no preamble:
{"subject_line":"Under 50 chars, punchy","preview_text":"Under 80 chars","body":"Full newsletter body in markdown, paragraphs separated by blank lines, 250-450 words total","cta_text":"Button text","cta_url":"https://www.bosskproductions.com/case-studies.html"}`;

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 2000,
        system: systemPrompt,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: [{ role: 'user', content: 'Research this month\'s AI/VFX news and write the newsletter.' }]
      })
    });

    const data = await response.json();
    const textBlocks = (data.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
    const clean = textBlocks.replace(/```json|```/g, '').trim();
    const draft = JSON.parse(clean);

    // Save as a draft — status stays 'draft' so it never auto-sends.
    await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Newsletter',
        subject: draft.subject_line,
        preview_text: draft.preview_text,
        body: draft.body,
        cta_text: draft.cta_text,
        cta_url: draft.cta_url,
        header_image: '',
        body_images: '',
        video_url: '',
        status: 'draft'
      }),
      redirect: 'follow'
    });

    // Ping Slack so Jason knows a draft is waiting for review.
    if (SLACK_WEBHOOK) {
      await fetch(SLACK_WEBHOOK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          blocks: [
            { type: 'header', text: { type: 'plain_text', text: '📰 Monthly newsletter draft ready' } },
            { type: 'section', text: { type: 'mrkdwn', text: `*Subject:* ${draft.subject_line}\n*Preview:* ${draft.preview_text}` } },
            { type: 'section', text: { type: 'mrkdwn', text: 'Saved as a draft. Review, add an image, and hit Send from the admin panel when ready.' } },
            { type: 'context', elements: [{ type: 'mrkdwn', text: '<https://www.bosskproductions.com/admin.html|Open Admin>' }] }
          ]
        })
      });
    }

    return res.status(200).json({ success: true, draft });
  } catch (e) {
    console.error('Auto-draft error:', e.message);
    if (SLACK_WEBHOOK) {
      await fetch(SLACK_WEBHOOK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: `⚠️ Monthly newsletter auto-draft failed: ${e.message}` })
      }).catch(() => {});
    }
    return res.status(500).json({ error: e.message });
  }
}
