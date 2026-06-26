export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { name, company, license, c10, eg4cert, phone, email, laborOnly, panels, elecBid, mountBid, permitBid, timeline, notes } = req.body;

  if (!name || !email || !phone) return res.status(400).json({ error: 'Name, email and phone are required' });

  const RESEND_KEY = process.env.RESEND_API_KEY;
  const totalBid = (parseFloat(elecBid) || 0) + (parseFloat(mountBid) || 0) + (parseFloat(permitBid) || 0);

  try {
    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'Bossk Solar <notifications@bosskproductions.com>',
        to: 'team@bosskproductions.com',
        subject: `Solar Bid — ${name}${company ? ' / ' + company : ''} — $${totalBid.toLocaleString()}`,
        html: `
          <h2 style="color:#F59E0B;">New Solar Bid Submission</h2>
          <table style="border-collapse:collapse;width:100%;max-width:560px;font-family:sans-serif;">
            <tr><td style="padding:8px;color:#888;font-size:12px;width:160px">NAME</td><td style="padding:8px;">${name}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">COMPANY</td><td style="padding:8px;">${company || '—'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">CSLB LICENSE</td><td style="padding:8px;">${license || '—'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">C-10 LICENSED</td><td style="padding:8px;">${c10 ? '✓ Yes' : 'No'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">EG4 CERTIFIED</td><td style="padding:8px;">${eg4cert ? '✓ Yes' : 'No'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">PHONE</td><td style="padding:8px;">${phone}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">EMAIL</td><td style="padding:8px;"><a href="mailto:${email}">${email}</a></td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">LABOR ONLY?</td><td style="padding:8px;">${laborOnly || '—'}</td></tr>
            <tr style="background:#f9f9f9"><td style="padding:8px;color:#888;font-size:12px;">PROPOSED PANELS</td><td style="padding:8px;font-weight:700;">${panels || '—'}</td></tr>
            <tr style="background:#f9f9f9"><td style="padding:8px;color:#888;font-size:12px;">ELECTRICIAN BID</td><td style="padding:8px;font-weight:700;">$${parseFloat(elecBid || 0).toLocaleString()}</td></tr>
            <tr style="background:#f9f9f9"><td style="padding:8px;color:#888;font-size:12px;">MOUNTING BID</td><td style="padding:8px;font-weight:700;">$${parseFloat(mountBid || 0).toLocaleString()}</td></tr>
            <tr style="background:#f9f9f9"><td style="padding:8px;color:#888;font-size:12px;">PERMITS + ENGINEERING</td><td style="padding:8px;font-weight:700;">$${parseFloat(permitBid || 0).toLocaleString()}</td></tr>
            <tr style="background:#fff3cd"><td style="padding:8px;color:#888;font-size:12px;font-weight:700;">TOTAL LABOR BID</td><td style="padding:8px;font-size:16px;font-weight:900;color:#d97706;">$${totalBid.toLocaleString()}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">TIMELINE</td><td style="padding:8px;">${timeline || '—'} weeks to PTO</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;vertical-align:top">NOTES</td><td style="padding:8px;">${notes || '—'}</td></tr>
          </table>
        `
      })
    });
    return res.status(200).json({ success: true });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
