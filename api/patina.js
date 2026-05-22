export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, email } = req.body;
  if (!name || !email) {
    return res.status(400).json({ error: 'Name and email are required' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxEXavIf3HNYjEY16k28O3MnJv7WQLRwlFaPUDnMZKcsjnWrp3mjSydsU4mPA_UsbtP/exec';
  const RESEND_KEY = process.env.RESEND_API_KEY;

  // Save to sheet
  try {
    await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Patina',
        name,
        email
      }),
      redirect: 'follow'
    });
  } catch (e) {
    console.error('Sheet save error:', e.message);
  }

  // Send welcome email
  if (RESEND_KEY) {
    const htmlEmail = `
      <div style="background:#0a0a0a;padding:40px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
        <div style="max-width:600px;margin:0 auto;">
          <p style="color:#f0ede8;font-size:16px;line-height:1.7;margin:0 0 16px;">Thanks for downloading PATINA — Production Color Conform for AI Generated Video.</p>
          <p style="color:#f0ede8;font-size:16px;line-height:1.7;margin:0 0 16px;">You're one of the first to get your hands on this. PATINA was built by filmmakers to solve a real production problem: AI-generated video looks like AI-generated video. PATINA fixes that.</p>
          <p style="color:#ff6b35;font-size:14px;font-weight:700;letter-spacing:0.5px;margin:28px 0 12px;">YOUR DOWNLOAD LINKS (THESE DON'T EXPIRE)</p>
          <div style="margin:0 0 24px;">
            <a href="https://github.com/BosskVFX/bossk-productions/releases/download/patina-v1.0.0/BOSSK_PATINA_v1.0.0.zip" style="display:inline-block;background:#ff6b35;color:#0a0a0a;padding:12px 24px;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:0.5px;margin:0 8px 8px 0;">WINDOWS 11</a>
            <a href="https://github.com/BosskVFX/bossk-productions/releases/download/patina-v1.0.0/Patina-arm64.dmg" style="display:inline-block;background:#ff6b35;color:#0a0a0a;padding:12px 24px;text-decoration:none;font-weight:700;font-size:13px;letter-spacing:0.5px;margin:0 8px 8px 0;">MAC OS (M1–M5)</a>
          </div>
          <p style="color:#f0ede8;font-size:16px;line-height:1.7;margin:0 0 16px;">If you run into any issues or have feedback, reply directly to this email. We read everything.</p>
          <div style="border-top:1px solid rgba(255,255,255,0.1);margin-top:30px;padding-top:20px;">
            <p style="color:#8a8680;font-size:13px;margin:0;">— The Bossk Productions Team</p>
            <p style="color:#8a8680;font-size:12px;margin:4px 0 0;"><a href="https://www.bosskproductions.com" style="color:#8a8680;">bosskproductions.com</a></p>
          </div>
        </div>
      </div>
    `;

    try {
      await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${RESEND_KEY}`
        },
        body: JSON.stringify({
          from: 'Bossk Productions <team@bosskproductions.com>',
          to: email,
          subject: 'Welcome to PATINA',
          html: htmlEmail
        })
      });
    } catch (e) {
      console.error('Email send error:', e.message);
    }
  }

  return res.status(200).json({ success: true });
}
