export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const SHEET_URL = process.env.DRIVE_IMAGES_URL;

  if (!SHEET_URL) {
    return res.status(500).json({ error: 'DRIVE_IMAGES_URL not set' });
  }

  try {
    const r = await fetch(SHEET_URL, { redirect: 'follow' });
    const data = await r.text();
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 's-maxage=30, stale-while-revalidate');
    return res.status(200).send(data);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
