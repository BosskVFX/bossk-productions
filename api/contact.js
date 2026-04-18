export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, email, company, service, message } = req.body;

  if (!name || !email) {
    return res.status(400).json({ error: 'Name and email are required' });
  }

  const RESEND_KEY = process.env.RESEND_API_KEY;
  const SHEET_URL = process.env.GOOGLE_SHEET_URL;
  const BEEHIIV_KEY = process.env.BEEHIIV_API_KEY;
  const BEEHIIV_PUB = process.env.BEEHIIV_PUB_ID;

  const results = {
    emailSent: false,
    autoReplySent: false,
    sheetLogged: false,
    newsletterAdded: false,
    errors: []
  };

  // 1. Send notification email to Bossk team
  try {
    const emailRes = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${RESEND_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: 'Bossk Productions <notifications@bosskproductions.com>',
        to: 'team@bosskproductions.com',
        subject: `New Inquiry: ${name}${company ? ' — ' + company : ''}`,
        html: `
          <h2 style="color:#ff6b35;">New inquiry from bosskproductions.com</h2>
          <table style="border-collapse:collapse;width:100%;max-width:500px;">
            <tr><td style="padding:8px;color:#888;font-size:12px;">NAME</td><td style="padding:8px;">${name}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">COMPANY</td><td style="padding:8px;">${company || '—'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">EMAIL</td><td style="padding:8px;"><a href="mailto:${email}">${email}</a></td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">SERVICE</td><td style="padding:8px;">${service || '—'}</td></tr>
            <tr><td style="padding:8px;color:#888;font-size:12px;">MESSAGE</td><td style="padding:8px;">${message || '—'}</td></tr>
          </table>
        `
      })
    });
    const emailData = await emailRes.json();
    console.log('TEAM EMAIL:', JSON.stringify(emailData));
    results.emailSent = emailRes.ok;
    if (!emailRes.ok) results.errors.push('team_email: ' + JSON.stringify(emailData));
  } catch (e) {
    console.error('TEAM EMAIL ERROR:', e.message);
    results.errors.push('team_email_catch: ' + e.message);
  }

  // 2. Send auto-reply to the person who submitted
  try {
    const replyRes = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${RESEND_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: 'Bossk Productions <hello@bosskproductions.com>',
        to: email,
        subject: 'We got your message — Bossk Productions',
        html: `
          <div style="font-family:sans-serif;max-width:520px;margin:0 auto;color:#333;">
            <h2 style="color:#ff6b35;margin-bottom:4px;">Thanks for reaching out, ${name}.</h2>
            <p style="color:#666;font-size:15px;line-height:1.7;">
              We received your inquiry and will get back to you within 24 hours.
            </p>
            <p style="color:#666;font-size:15px;line-height:1.7;">
              In the meantime, check out our latest work at
              <a href="https://www.bosskproductions.com/#work" style="color:#ff6b35;">bosskproductions.com</a>.
            </p>
            <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
            <p style="color:#999;font-size:12px;">
              Bossk Productions — Content at the Speed of Thought<br>
              Los Angeles, CA • Worldwide
            </p>
          </div>
        `
      })
    });
    const replyData = await replyRes.json();
    console.log('AUTO REPLY:', JSON.stringify(replyData));
    results.autoReplySent = replyRes.ok;
    if (!replyRes.ok) results.errors.push('auto_reply: ' + JSON.stringify(replyData));
  } catch (e) {
    console.error('AUTO REPLY ERROR:', e.message);
    results.errors.push('auto_reply_catch: ' + e.message);
  }

  // 3. Log to Google Sheet
  console.log('SHEET_URL value:', SHEET_URL ? 'SET (' + SHEET_URL.substring(0, 50) + '...)' : 'NOT SET');
  if (SHEET_URL) {
    try {
      const sheetRes = await fetch(SHEET_URL, {
        method: 'POST',
        body: JSON.stringify({ name, email, company, service, message }),
        headers: { 'Content-Type': 'text/plain' },
        redirect: 'follow'
      });
      const sheetText = await sheetRes.text();
      console.log('SHEET RESPONSE:', sheetRes.status, sheetText);
      results.sheetLogged = true;
    } catch (e) {
      console.error('SHEET ERROR:', e.message);
      results.errors.push('sheet: ' + e.message);
    }
  } else {
    results.errors.push('GOOGLE_SHEET_URL env var not set');
  }

  // 4. Add to Beehiiv newsletter
  console.log('BEEHIIV KEY:', BEEHIIV_KEY ? 'SET' : 'NOT SET');
  console.log('BEEHIIV PUB:', BEEHIIV_PUB ? BEEHIIV_PUB : 'NOT SET');
  if (BEEHIIV_KEY && BEEHIIV_PUB) {
    try {
      const bhRes = await fetch(`https://api.beehiiv.com/v2/publications/${BEEHIIV_PUB}/subscriptions`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${BEEHIIV_KEY}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          email: email,
          utm_source: 'website_contact_form',
          referring_site: 'bosskproductions.com',
          double_opt_override: 'off',
          send_welcome_email: false
        })
      });
      const bhData = await bhRes.json();
      console.log('BEEHIIV RESPONSE:', JSON.stringify(bhData));
      results.newsletterAdded = bhRes.ok || bhRes.status === 201;
      if (!bhRes.ok && bhRes.status !== 201) results.errors.push('beehiiv: ' + JSON.stringify(bhData));
    } catch (e) {
      console.error('BEEHIIV ERROR:', e.message);
      results.errors.push('beehiiv: ' + e.message);
    }
  } else {
    results.errors.push('BEEHIIV env vars not set');
  }

  console.log('FINAL RESULTS:', JSON.stringify(results));

  return res.status(200).json({
    success: true,
    ...results
  });
}
