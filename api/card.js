// QR card redirect — bosskproductions.com/card
//
// Logs every scan to Vercel Observability and 302-redirects to the destination.
// To change where /card points, update the CARD_DESTINATION env var in
// Vercel → Project Settings → Environment Variables, then redeploy.
// Falls back to https://princesslumi.com/ if the env var is unset.

const DEFAULT_DESTINATION = 'https://princesslumi.com/';

export default function handler(req, res) {
  const destination = process.env.CARD_DESTINATION || DEFAULT_DESTINATION;

  const scan = {
    event: 'card_scan',
    timestamp: new Date().toISOString(),
    destination,
    userAgent: req.headers['user-agent'] || null,
    referer: req.headers.referer || null,
    country: req.headers['x-vercel-ip-country'] || null,
    region: req.headers['x-vercel-ip-country-region'] || null,
    city: req.headers['x-vercel-ip-city'] ? decodeURIComponent(req.headers['x-vercel-ip-city']) : null,
    ip: req.headers['x-forwarded-for']?.split(',')[0]?.trim() || null,
  };

  // Visible in Vercel → Logs / Observability. Filter by [card] to see all scans.
  console.log('[card]', JSON.stringify(scan));

  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('X-Robots-Tag', 'noindex');
  res.redirect(302, destination);
}
