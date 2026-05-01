export default async function handler(req, res) {
  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxEXavIf3HNYjEY16k28O3MnJv7WQLRwlFaPUDnMZKcsjnWrp3mjSydsU4mPA_UsbtP/exec';

  if (req.method === 'GET') {
    const { slug } = req.query;
    if (!slug) return res.status(400).json({ error: 'Slug is required' });

    try {
      const r = await fetch(SHEET_URL + '?sheet=Clients', { redirect: 'follow' });
      const data = await r.json();
      const project = data.find(d => {
        const rowSlug = (d['Slug'] || d['Slug '] || '').trim();
        const active = String(d['Active'] || d['Active '] || '').trim().toUpperCase();
        return rowSlug === slug && active === 'TRUE';
      });
      if (!project) return res.status(404).json({ error: 'Project not found' });
      // Trim all keys
      const clean = {};
      for (const k in project) clean[k.trim()] = project[k];
      return res.status(200).json(clean);
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  if (req.method === 'POST') {
    const { slug, client, project, image, frameio, docs, sheets, links, notes } = req.body;
    if (!slug || !client || !project) {
      return res.status(400).json({ error: 'Slug, client, and project are required' });
    }

    try {
      await fetch(SHEET_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({
          _sheet: 'Clients',
          slug, client, project, image,
          frameio, docs, sheets, links, notes
        }),
        redirect: 'follow'
      });
      return res.status(200).json({ success: true, slug });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  return res.status(405).json({ error: 'Method not allowed' });
}
