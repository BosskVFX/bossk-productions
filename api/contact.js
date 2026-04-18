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

  let emailSent = false;
  let autoReplySent = false;
  let sheetLogged = false;
  let newsletterAdded = false;

  // 1. Send notification email to Bossk team
  try {
    const emailRes = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${RESEND_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        from: 'Bossk Website <onboarding@resend.dev>',
        to: 'bosskproductions@gmail.com',
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
    console.log('Team email response:', JSON.stringify(emailData));
    emailSent = emailRes.ok;
  } catch (e) {
    console.error('Team email error:', e);
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
        from: 'Bossk Productions <onboarding@resend.dev>',
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
    console.log('Auto-reply response:', JSON.stringify(replyData));
    autoReplySent = replyRes.ok;
  } catch (e) {
    console.error('Auto-reply error:', e);
  }

  // 3. Log to Google Sheet
  if (SHEET_URL) {
    try {
      await fetch(SHEET_URL, {
        method: 'POST',
        body: JSON.stringify({ name, email, company, service, message }),
        headers: { 'Content-Type': 'text/plain' },
        redirect: 'follow'
      });
      sheetLogged = true;
      console.log('Sheet logged successfully');
    } catch (e) {
      console.error('Sheet error:', e.message);
    }
  } else {
    console.error('GOOGLE_SHEET_URL env var not set');
  }

  // 4. Add to Beehiiv newsletter
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
          send_welcome_email: false,
          custom_fields: [
            { name: 'Full Name', value: name },
            { name: 'Company', value: company || '' }
          ]
        })
      });
      const bhData = await bhRes.json();
      console.log('Beehiiv response:', JSON.stringify(bhData));
      newsletterAdded = bhRes.ok || bhRes.status === 201;
    } catch (e) {
      console.error('Beehiiv error:', e.message);
    }
  } else {
    console.error('BEEHIIV env vars not set');
  }

  return res.status(200).json({
    success: true,
    emailSent,
    autoReplySent,
    sheetLogged,
    newsletterAdded
  });
}
