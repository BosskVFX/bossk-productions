export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { email, name } = req.body;
  if (!email) {
    return res.status(400).json({ error: 'Email is required' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbwEylrOTVMnky-UyvMZL19HDokXjNdRhKCy_KC_7Y6eH7xg9Q47Qq191_zbW5HILY9h/exec';

  try {
    const r = await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Subscribers',
        email,
        name: name || ''
      }),
      redirect: 'follow'
    });
    return res.status(200).json({ success: true });
  } catch (e) {
    console.error('Subscribe error:', e.message);
    return res.status(500).json({ error: 'Failed to subscribe' });
  }
}
