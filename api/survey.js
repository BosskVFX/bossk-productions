export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, practice, contact, answers } = req.body;

  if (!Array.isArray(answers) || answers.length === 0) {
    return res.status(400).json({ error: 'No answers provided' });
  }

  const RESEND_KEY = process.env.RESEND_API_KEY;
  const results = { emailSent: false, errors: [] };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const row = (label, val, highlight) => `
    <tr style="${highlight ? 'background:#EFF6FF;' : ''}">
      <td style="padding:10px 14px;color:#64748B;font-size:12px;font-weight:600;width:280px;border-bottom:1px solid #E2E8F0;vertical-align:top;">${esc(label)}</td>
      <td style="padding:10px 14px;color:${val === '—' ? '#94A3B8' : '#0F172A'};font-size:13.5px;font-weight:${highlight ? '800' : '500'};border-bottom:1px solid #E2E8F0;">${esc(val)}</td>
    </tr>`;

  const answerRows = answers
    .slice(0, 40) // sanity cap
    .map((a) => row(a.question, a.answer))
    .join('');

  const who = [name, practice].filter(Boolean).join(' — ') || 'Anonymous';
  const answeredCount = answers.filter((a) => a.answer && a.answer !== '—').length;

  const html = `
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:#F8FAFC;padding:24px;max-width:680px;margin:0 auto;">
      <div style="background:#2563EB;border-radius:10px;padding:16px 20px;margin-bottom:20px;">
        <div style="color:#BFDBFE;font-size:11px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;margin-bottom:2px;">DialedAds — Survey Response</div>
        <div style="color:#fff;font-size:20px;font-weight:900;">${esc(who)}</div>
        <div style="color:#BFDBFE;font-size:12px;margin-top:4px;">${answeredCount} of ${answers.length} questions answered</div>
      </div>
      <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;">
        ${row('Name', name || '—', true)}
        ${row('Practice', practice || '—', true)}
        ${row('Contact', contact || '—', true)}
        ${answerRows}
      </table>
    </div>`;

  try {
    const emailRes = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'DialedAds Survey <notifications@bosskproductions.com>',
        to: 'team@bosskproductions.com',
        ...(contact && contact.includes('@') ? { reply_to: contact } : {}),
        subject: `DialedAds Survey — ${who}`,
        html
      })
    });
    const emailData = await emailRes.json();
    results.emailSent = emailRes.ok;
    if (!emailRes.ok) results.errors.push('email: ' + JSON.stringify(emailData));
  } catch (e) {
    results.errors.push('email_catch: ' + e.message);
  }

  if (!results.emailSent) {
    return res.status(502).json({ success: false, ...results });
  }
  return res.status(200).json({ success: true, ...results });
}
