const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, email, company, service, message, answers } = req.body;
  const isSurvey = Array.isArray(answers);

  if (isSurvey && answers.length === 0) {
    return res.status(400).json({ error: 'No answers provided' });
  }
  if (!isSurvey && (!name || !email)) {
    return res.status(400).json({ error: 'Name and email are required' });
  }

  const RESEND_KEY = process.env.RESEND_API_KEY;
  const SHEET_URL = process.env.GOOGLE_SHEET_URL;
  const BEEHIIV_KEY = process.env.BEEHIIV_API_KEY;
  const BEEHIIV_PUB = process.env.BEEHIIV_PUB_ID;

  const isSolarBid = service && service.startsWith('Solar Bid');

  const results = { emailSent: false, autoReplySent: false, sheetLogged: false, errors: [] };

  // ─── BUILD EMAILS ────────────────────────────────────────────────────────────
  let teamSubject, teamHtml, replyHtml;

  if (isSurvey) {
    const row = (label, val, highlight) => `
      <tr style="${highlight ? 'background:#EFF6FF;' : ''}">
        <td style="padding:10px 14px;color:#64748B;font-size:12px;font-weight:600;width:280px;border-bottom:1px solid #E2E8F0;vertical-align:top;">${esc(label)}</td>
        <td style="padding:10px 14px;color:${val === '—' ? '#94A3B8' : '#0F172A'};font-size:13.5px;font-weight:${highlight ? '800' : '500'};border-bottom:1px solid #E2E8F0;">${esc(val)}</td>
      </tr>`;
    const answerRows = answers.slice(0, 40).map((a) => row(a.question, a.answer)).join('');
    const answeredCount = answers.filter((a) => a.answer && a.answer !== '—').length;

    teamSubject = 'Survey Response';
    teamHtml = `
      <div style="font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:#F8FAFC;padding:24px;max-width:680px;margin:0 auto;">
        <div style="background:#2563EB;border-radius:10px;padding:16px 20px;margin-bottom:20px;">
          <div style="color:#BFDBFE;font-size:11px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;margin-bottom:2px;">New Survey Response</div>
          <div style="color:#fff;font-size:20px;font-weight:900;">Survey Response</div>
          <div style="color:#BFDBFE;font-size:12px;margin-top:4px;">${answeredCount} of ${answers.length} questions answered</div>
        </div>
        <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;">
          ${answerRows}
        </table>
      </div>`;
  } else if (isSolarBid) {
    const b = req.body;
    const fmt = (n) => n > 0 ? '$' + Number(n).toLocaleString() : '—';
    const cell = (label, val, highlight) =>
      `<tr style="${highlight ? 'background:#1a2a1a;' : ''}">
        <td style="padding:10px 14px;color:#94A3B8;font-size:12px;font-weight:600;white-space:nowrap;width:200px;border-bottom:1px solid #1E3048;">${label}</td>
        <td style="padding:10px 14px;color:${highlight ? '#10B981' : '#F1F5F9'};font-size:${highlight ? '16px' : '13px'};font-weight:${highlight ? '900' : '500'};border-bottom:1px solid #1E3048;">${val}</td>
      </tr>`;

    const bidTable = `
      <table style="width:100%;border-collapse:collapse;border:1px solid #243447;border-radius:10px;overflow:hidden;">
        ${cell('Contractor', `${name}${b.company && b.company !== '—' ? ' — ' + b.company : ''}`)}
        ${cell('CSLB License', b.bid_license||'—')}
        ${cell('C-10 Licensed', b.bid_c10 === 'Yes' ? '✓ Yes' : '✗ No')}
        ${cell('EG4 Certified', b.bid_eg4cert === 'Yes' ? '✓ Yes' : '✗ No')}
        ${cell('Phone', b.bid_phone||'—')}
        ${cell('Email', email)}
        ${cell('Labor Only?', b.bid_laborOnly||'—')}
        <tr><td colspan="2" style="padding:6px 14px;background:#0F1923;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#F59E0B;">BID BREAKDOWN</td></tr>
        ${cell('Electrician Labor', fmt(b.bid_elec))}
        ${cell('Mounting Crew', fmt(b.bid_mount))}
        ${cell('Permits + PE Engineering', fmt(b.bid_permit))}
        ${cell('TOTAL LABOR BID', fmt(b.bid_total), true)}
        ${cell('Timeline', b.bid_timeline||'—')}
        ${b.bid_notes && b.bid_notes !== '—' ? cell('Notes', b.bid_notes) : ''}
      </table>`;

    teamSubject = `Solar Bid — ${name}${b.company && b.company !== '—' ? ' / ' + b.company : ''} — ${fmt(b.bid_total)}`;

    teamHtml = `
      <div style="font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:#0F1923;padding:24px;max-width:600px;margin:0 auto;">
        <div style="background:#F59E0B;border-radius:8px;padding:14px 18px;margin-bottom:20px;">
          <div style="color:#0F1923;font-size:11px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;margin-bottom:2px;">New Submission</div>
          <div style="color:#0F1923;font-size:20px;font-weight:900;">Solar Bid — 5541 Willis Ave</div>
        </div>
        ${bidTable}
        <div style="margin-top:16px;padding:12px 14px;background:#1A2B3C;border-radius:8px;border:1px solid #243447;">
          <a href="mailto:${email}" style="color:#3B82F6;font-size:13px;">Reply directly to contractor →</a>
        </div>
      </div>`;

    replyHtml = `
      <div style="font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:#0F1923;padding:24px;max-width:600px;margin:0 auto;">
        <div style="background:#10B981;border-radius:8px;padding:14px 18px;margin-bottom:20px;">
          <div style="color:#0F1923;font-size:11px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;margin-bottom:2px;">Bid Received</div>
          <div style="color:#0F1923;font-size:20px;font-weight:900;">Thanks, ${name}.</div>
        </div>
        <p style="color:#94A3B8;font-size:13px;line-height:1.7;margin-bottom:20px;">
          We received your bid for <strong style="color:#F1F5F9;">5541 Willis Ave, Sherman Oaks, CA 91411</strong>. 
          Here's a summary of what you submitted. We'll be in touch within 24 hours.
        </p>
        ${bidTable}
        <div style="margin-top:20px;padding:16px;background:#1A2B3C;border-radius:8px;border:1px solid #243447;text-align:center;">
          <div style="color:#64748B;font-size:11px;margin-bottom:8px;">View the full project brief</div>
          <a href="https://bosskproductions.com/solar-q26" style="display:inline-block;background:#F59E0B;color:#0F1923;padding:10px 24px;border-radius:7px;font-weight:800;font-size:13px;text-decoration:none;">View Project Brief →</a>
        </div>
        <div style="margin-top:16px;color:#475569;font-size:11px;text-align:center;">
          Bossk Productions LLC · 5541 Willis Ave, Sherman Oaks, CA 91411
        </div>
      </div>`;

  } else {
    // Standard contact form
    teamSubject = `New Inquiry: ${name}${company ? ' — ' + company : ''}`;
    teamHtml = `
      <h2 style="color:#ff6b35;">New inquiry from bosskproductions.com</h2>
      <table style="border-collapse:collapse;width:100%;max-width:500px;">
        <tr><td style="padding:8px;color:#888;font-size:12px;">NAME</td><td style="padding:8px;">${name}</td></tr>
        <tr><td style="padding:8px;color:#888;font-size:12px;">COMPANY</td><td style="padding:8px;">${company || '—'}</td></tr>
        <tr><td style="padding:8px;color:#888;font-size:12px;">EMAIL</td><td style="padding:8px;"><a href="mailto:${email}">${email}</a></td></tr>
        <tr><td style="padding:8px;color:#888;font-size:12px;">SERVICE</td><td style="padding:8px;">${service || '—'}</td></tr>
        <tr><td style="padding:8px;color:#888;font-size:12px;">MESSAGE</td><td style="padding:8px;">${message || '—'}</td></tr>
      </table>`;
    replyHtml = `
      <div style="font-family:sans-serif;max-width:520px;margin:0 auto;color:#333;">
        <h2 style="color:#ff6b35;margin-bottom:4px;">Thanks for reaching out, ${name}.</h2>
        <p style="color:#666;font-size:15px;line-height:1.7;">We received your inquiry and will get back to you within 24 hours.</p>
        <p style="color:#666;font-size:15px;line-height:1.7;">In the meantime, check out our latest work at <a href="https://www.bosskproductions.com/#work" style="color:#ff6b35;">bosskproductions.com</a>.</p>
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="color:#999;font-size:12px;">Bossk Productions — Content at the Speed of Thought<br>Los Angeles, CA • Worldwide</p>
      </div>`;
  }

  // ─── SEND TEAM EMAIL ─────────────────────────────────────────────────────────
  try {
    const emailRes = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: 'Bossk Productions <notifications@bosskproductions.com>',
        to: 'team@bosskproductions.com',
        ...(email ? { reply_to: email } : {}),
        subject: teamSubject,
        html: teamHtml
      })
    });
    const emailData = await emailRes.json();
    results.emailSent = emailRes.ok;
    if (!emailRes.ok) results.errors.push('team_email: ' + JSON.stringify(emailData));
  } catch (e) {
    results.errors.push('team_email_catch: ' + e.message);
  }

  // ─── SEND AUTO-REPLY (skipped for surveys — no guaranteed reply address) ─────
  if (!isSurvey) {
    try {
      const replyRes = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${RESEND_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: isSolarBid
            ? 'Bossk Productions Solar <hello@bosskproductions.com>'
            : 'Bossk Productions <hello@bosskproductions.com>',
          to: email,
          subject: isSolarBid
            ? 'Your Solar Bid — 5541 Willis Ave, Sherman Oaks'
            : 'We got your message — Bossk Productions',
          html: replyHtml
        })
      });
      const replyData = await replyRes.json();
      results.autoReplySent = replyRes.ok;
      if (!replyRes.ok) results.errors.push('auto_reply: ' + JSON.stringify(replyData));
    } catch (e) {
      results.errors.push('auto_reply_catch: ' + e.message);
    }
  }

  // ─── LOG TO GOOGLE SHEET (non-surveys only — different schema) ──────────────
  if (SHEET_URL && !isSurvey) {
    try {
      await fetch(SHEET_URL, {
        method: 'POST',
        body: JSON.stringify({ name, email, company, service, message }),
        headers: { 'Content-Type': 'text/plain' },
        redirect: 'follow'
      });
      results.sheetLogged = true;
    } catch (e) {
      results.errors.push('sheet: ' + e.message);
    }
  }

  // ─── NEWSLETTER (non-bids, non-surveys only) ────────────────────────────────
  if (!isSolarBid && !isSurvey && BEEHIIV_KEY && BEEHIIV_PUB) {
    try {
      await fetch(`https://api.beehiiv.com/v2/publications/${BEEHIIV_PUB}/subscriptions`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${BEEHIIV_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, utm_source: 'website_contact_form', referring_site: 'bosskproductions.com', double_opt_override: 'off', send_welcome_email: false })
      });
      results.newsletterAdded = true;
    } catch (e) {
      results.errors.push('beehiiv: ' + e.message);
    }
  }

  if (isSurvey && !results.emailSent) {
    return res.status(502).json({ success: false, ...results });
  }
  return res.status(200).json({ success: true, ...results });
}
