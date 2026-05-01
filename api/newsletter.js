export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxEXavIf3HNYjEY16k28O3MnJv7WQLRwlFaPUDnMZKcsjnWrp3mjSydsU4mPA_UsbtP/exec';
  const RESEND_KEY = process.env.RESEND_API_KEY;
  const { subject, preview_text, body, cta_text, cta_url, header_image, body_images, video_url, action } = req.body;

  if (!subject || !body) {
    return res.status(400).json({ error: 'Subject and body are required' });
  }

  // Save to Newsletter sheet
  try {
    await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Newsletter',
        subject, preview_text: preview_text || '', body,
        cta_text: cta_text || '', cta_url: cta_url || '',
        header_image: header_image || '',
        body_images: body_images || '',
        video_url: video_url || '',
        status: action || 'draft'
      }),
      redirect: 'follow'
    });
  } catch (e) {
    console.error('Sheet save error:', e.message);
  }

  // If draft, stop here
  if (action === 'draft') {
    return res.status(200).json({ success: true, mode: 'draft' });
  }

  // Get active subscribers
  if (!RESEND_KEY) {
    return res.status(500).json({ error: 'RESEND_API_KEY not set' });
  }

  let subscribers = [];
  try {
    const subRes = await fetch(SHEET_URL + '?sheet=Subscribers', { redirect: 'follow' });
    const subData = await subRes.json();
    subscribers = subData.filter(s => String(s.Active).toUpperCase() === 'TRUE').map(s => s.Email).filter(Boolean);
  } catch (e) {
    console.error('Fetch subscribers error:', e.message);
    return res.status(500).json({ error: 'Failed to fetch subscribers' });
  }

  if (!subscribers.length) {
    return res.status(400).json({ error: 'No active subscribers found' });
  }

  // Build HTML email
  let htmlBody = '';
  if (header_image) {
    htmlBody += `<img src="${header_image}" alt="Newsletter header" style="width:100%;max-width:600px;height:auto;margin-bottom:20px;">`;
  }

  const bodyImgList = body_images ? body_images.split(',').filter(Boolean) : [];
  const paragraphs = body.split('\n\n').filter(Boolean);
  let imgIdx = 0;

  paragraphs.forEach((p, i) => {
    if (p.startsWith('### ')) htmlBody += `<h3 style="color:#f0ede8;font-size:18px;margin:20px 0 8px;">${p.slice(4)}</h3>`;
    else if (p.startsWith('## ')) htmlBody += `<h2 style="color:#f0ede8;font-size:22px;margin:24px 0 10px;">${p.slice(3)}</h2>`;
    else if (p.startsWith('# ')) htmlBody += `<h1 style="color:#f0ede8;font-size:26px;margin:28px 0 12px;">${p.slice(2)}</h1>`;
    else {
      let text = p.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      htmlBody += `<p style="color:#f0ede8;font-size:16px;line-height:1.7;margin:0 0 16px;">${text}</p>`;
    }
    if (imgIdx < bodyImgList.length && (i + 1) % 2 === 0) {
      htmlBody += `<img src="${bodyImgList[imgIdx]}" alt="" style="width:100%;max-width:600px;height:auto;margin:16px 0;">`;
      imgIdx++;
    }
  });
  while (imgIdx < bodyImgList.length) {
    htmlBody += `<img src="${bodyImgList[imgIdx]}" alt="" style="width:100%;max-width:600px;height:auto;margin:16px 0;">`;
    imgIdx++;
  }

  // Video thumbnail
  if (video_url) {
    const thumbImg = header_image || (bodyImgList.length ? bodyImgList[0] : '');
    if (thumbImg) {
      const thumbUrl = `https://www.bosskproductions.com/api/thumb?img=${encodeURIComponent(thumbImg)}`;
      htmlBody += `
        <div style="margin:24px 0;text-align:center;">
          <a href="${video_url}" target="_blank" style="text-decoration:none;">
            <img src="${thumbUrl}" alt="Watch video" style="width:100%;max-width:600px;height:auto;display:block;margin:0 auto;">
          </a>
          <p style="color:#8a8680;font-size:13px;margin-top:8px;">Click to watch</p>
        </div>`;
    }
  }

  if (cta_text && cta_url) {
    htmlBody += `<div style="text-align:center;margin:30px 0;"><a href="${cta_url}" style="background:#ff6b35;color:#0a0a0a;padding:14px 32px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:1px;text-transform:uppercase;display:inline-block;">${cta_text}</a></div>`;
  }

  // Send to each subscriber individually (for unsubscribe link)
  let sent = 0;
  let errors = [];

  for (const email of subscribers) {
    const unsub = `https://www.bosskproductions.com/unsubscribe.html?email=${encodeURIComponent(email)}`;
    const fullHtml = `
      <div style="background:#0a0a0a;padding:40px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
        <div style="max-width:600px;margin:0 auto;">
          ${htmlBody}
          <div style="border-top:1px solid rgba(255,255,255,0.1);margin-top:30px;padding-top:20px;">
            <p style="color:#8a8680;font-size:12px;text-align:center;">Bossk Productions — Los Angeles, CA</p>
            <p style="text-align:center;margin-top:8px;"><a href="${unsub}" style="color:#8a8680;font-size:11px;text-decoration:underline;">Unsubscribe</a></p>
          </div>
        </div>
      </div>
    `;

    try {
      const sendRes = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${RESEND_KEY}`
        },
        body: JSON.stringify({
          from: 'The Bossk Brief <newsletter@bosskproductions.com>',
          to: email,
          subject: subject,
          html: fullHtml
        })
      });
      if (sendRes.ok) sent++;
      else {
        const err = await sendRes.json();
        errors.push({ email, error: err });
      }
    } catch (e) {
      errors.push({ email, error: e.message });
    }
  }

  console.log(`Newsletter sent: ${sent}/${subscribers.length}`, errors.length ? errors : '');
  return res.status(200).json({ success: true, sent, total: subscribers.length, errors: errors.length });
}
