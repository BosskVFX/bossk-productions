export default async function handler(req, res) {
  // Only allow POST
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, email, company, service, message } = req.body;

  if (!name || !email) {
    return res.status(400).json({ error: 'Name and email are required' });
  }

  const RESEND_KEY = process.env.RESEND_API_KEY;
  const SHEET_URL = process.env.GOOGLE_SHEET_URL;

  // Send email via Resend
  let emailSent = false;
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
          <h2>New inquiry from bosskproductions.com</h2>
          <p><strong>Name:</strong> ${name}</p>
          <p><strong>Company:</strong> ${company || 'Not provided'}</p>
          <p><strong>Email:</strong> ${email}</p>
          <p><strong>Service:</strong> ${service || 'Not selected'}</p>
          <p><strong>Message:</strong></p>
          <p>${message || 'No message provided'}</p>
        `
      })
    });
    const emailData = await emailRes.json();
    console.log('Resend response:', JSON.stringify(emailData));
    emailSent = emailRes.ok;
  } catch (e) {
    console.error('Resend error:', e);
  }

  // Log to Google Sheet
  let sheetLogged = false;
  try {
    const sheetRes = await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ name, email, company, service, message }),
      redirect: 'follow'
    });
    sheetLogged = true;
  } catch (e) {
    console.error('Sheet error:', e);
  }

  return res.status(200).json({
    success: true,
    emailSent,
    sheetLogged
  });
}
