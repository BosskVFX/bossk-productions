export default async function handler(req, res) {
  if (req.method === 'GET') {
    // Fetch published case studies
    try {
      const sheetRes = await fetch('https://script.google.com/macros/s/AKfycbzIbheoTc2SZgcFODz70eqyAknf6xcHGJrx5clHh7_vN96aJO1_tMBJfuQh7CYbKLNt/exec', {
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
    // Publish a new case study
    const { title, subtitle, client_type, services_used, challenge, solution, result, pull_quote, seo_description, hero_image, extra_images, video_url, status, schedule_date } = req.body;

    if (!title) {
      return res.status(400).json({ error: 'Title is required' });
    }

    try {
      await fetch('https://script.google.com/macros/s/AKfycbzIbheoTc2SZgcFODz70eqyAknf6xcHGJrx5clHh7_vN96aJO1_tMBJfuQh7CYbKLNt/exec', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({
          title,
          subtitle,
          client_type,
          services: Array.isArray(services_used) ? services_used.join(', ') : services_used || '',
          challenge,
          solution,
          result,
          quote: pull_quote,
          seo_description,
          hero_image: hero_image || '',
          extra_images: extra_images || '',
          video_url: video_url || '',
          status: status || 'publish',
          schedule_date: schedule_date || ''
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
