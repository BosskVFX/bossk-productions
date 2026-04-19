export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbz1ow0_5Pc1RfsYL5i9IFupQq1RmYrQ14IhiFHU7Jr0gOB4XJWA4UekggBoYZ-9ADX_/exec';

  try {
    const r = await fetch(SHEET_URL + '?all=true', { redirect: 'follow' });
    const data = await r.text();
    res.setHeader('Content-Type', 'application/json');
    return res.status(200).send(data);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
