export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxcWN1RPDyte8p_L_VtPfl7Nau3ATCGj2Jd9EUP4kNS2XYF2L8Cc1LfdOaOxcwp2gQ_/exec';
  const sheet = req.query.sheet || 'CaseStudy';

  try {
    const r = await fetch(SHEET_URL + `?all=true&sheet=${sheet}`, { redirect: 'follow' });
    const data = await r.text();
    res.setHeader('Content-Type', 'application/json');
    return res.status(200).send(data);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
