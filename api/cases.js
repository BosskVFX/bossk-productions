export default async function handler(req, res) {
  if (req.method === 'GET') {
    // Fetch published case studies
    try {
      const sheetRes = await fetch('https://script.google.com/macros/s/AKfycbwEylrOTVMnky-UyvMZL19HDokXjNdRhKCy_KC_7Y6eH7xg9Q47Qq191_zbW5HILY9h/exec', {
        redirect: 'follow'
      });
      const data = await sheetRes.text();
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate');
      return res.status(200).send(data);
    } catch (e) {
      console.error('Fetch case studies error:', e.message);
      return res.status(500).json({ error: 'Failed to fetch case studies' });
    }
  }

  if (req.method === 'POST') {
    const body = req.body;
    const isNewsletter = body._sheet === 'Newsletter';

    if (!isNewsletter && !body.title) {
      return res.status(400).json({ error: 'Title is required' });
    }
    if (isNewsletter && !body.subject) {
      return res.status(400).json({ error: 'Subject is required' });
    }

    try {
      await fetch('https://script.google.com/macros/s/AKfycbwEylrOTVMnky-UyvMZL19HDokXjNdRhKCy_KC_7Y6eH7xg9Q47Qq191_zbW5HILY9h/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify(isNewsletter ? {
          _sheet: 'Newsletter',
          subject: body.subject || '',
          preview_text: body.preview_text || '',
          body: body.body || '',
          cta_text: body.cta_text || '',
          cta_url: body.cta_url || '',
          header_image: body.header_image || '',
          status: body.status || 'draft'
        } : {
          title: body.title,
          subtitle: body.subtitle,
          client_type: body.client_type,
          services: Array.isArray(body.services_used) ? body.services_used.join(', ') : body.services_used || '',
          challenge: body.challenge,
          solution: body.solution,
          result: body.result,
          quote: body.pull_quote,
          seo_description: body.seo_description,
          hero_image: body.hero_image || '',
          extra_images: body.extra_images || '',
          video_url: body.video_url || '',
          status: body.status || 'publish',
          schedule_date: body.schedule_date || ''
        }),
        redirect: 'follow'
      });
      return res.status(200).json({ success: true });
    } catch (e) {
      console.error('Publish error:', e.message);
      return res.status(500).json({ error: 'Failed to publish' });
    }
  }

  return res.status(405).json({ error: 'Method not allowed' });
}
