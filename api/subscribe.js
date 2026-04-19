export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { email } = req.body;

  if (!email) {
    return res.status(400).json({ error: 'Email is required' });
  }

  const BEEHIIV_KEY = process.env.BEEHIIV_API_KEY;
  const BEEHIIV_PUB = process.env.BEEHIIV_PUB_ID;

  if (!BEEHIIV_KEY || !BEEHIIV_PUB) {
    console.error('BEEHIIV env vars not set');
    return res.status(500).json({ error: 'Configuration error' });
  }

  try {
    const bhRes = await fetch(`https://api.beehiiv.com/v2/publications/${BEEHIIV_PUB}/subscriptions`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${BEEHIIV_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        email: email,
        utm_source: 'website_newsletter',
        referring_site: 'bosskproductions.com',
        double_opt_override: 'off',
        send_welcome_email: true
      })
    });
    const bhData = await bhRes.json();
    console.log('Newsletter subscribe:', JSON.stringify(bhData));

    return res.status(200).json({ success: true });
  } catch (e) {
    console.error('Newsletter error:', e.message);
    return res.status(500).json({ error: 'Failed to subscribe' });
  }
}
