export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxrtHey9q4KaeJoXZSaIb-6XWxhXciSHW51gKLpOrrQVhHOXTs_tABBAKanqfRPDIE3/exec';
  const { subject, preview_text, body, cta_text, cta_url, header_image, body_images, action } = req.body;

  if (!subject || !body) {
    return res.status(400).json({ error: 'Subject and body are required' });
  }

  // Save to Google Sheet
  try {
    await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Newsletter',
        subject,
        preview_text: preview_text || '',
        body,
        cta_text: cta_text || '',
        cta_url: cta_url || '',
        header_image: header_image || '',
        status: action || 'draft'
      }),
      redirect: 'follow'
    });
  } catch (e) {
    console.error('Sheet save error:', e.message);
  }

  // If draft only, stop here
  if (action === 'draft') {
    return res.status(200).json({ success: true, mode: 'draft' });
  }

  // Publish to Beehiiv
  const API_KEY = process.env.BEEHIIV_API_KEY;
  const PUB_ID = process.env.BEEHIIV_PUB_ID;

  if (!API_KEY || !PUB_ID) {
    return res.status(500).json({ error: 'Beehiiv credentials not set' });
  }

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

    // Insert a body image after every 2-3 paragraphs
    if (imgIdx < bodyImgList.length && (i + 1) % 2 === 0) {
      htmlBody += `<img src="${bodyImgList[imgIdx]}" alt="Newsletter image" style="width:100%;max-width:600px;height:auto;margin:16px 0;border-radius:2px;">`;
      imgIdx++;
    }
  });

  // Add any remaining images at the end
  while (imgIdx < bodyImgList.length) {
    htmlBody += `<img src="${bodyImgList[imgIdx]}" alt="Newsletter image" style="width:100%;max-width:600px;height:auto;margin:16px 0;border-radius:2px;">`;
    imgIdx++;
  }

  if (cta_text && cta_url) {
    htmlBody += `<div style="text-align:center;margin:30px 0;"><a href="${cta_url}" style="background:#ff6b35;color:#0a0a0a;padding:14px 32px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:1px;text-transform:uppercase;display:inline-block;">${cta_text}</a></div>`;
  }

  const fullHtml = `
    <div style="background:#0a0a0a;padding:40px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
      <div style="max-width:600px;margin:0 auto;">
        ${htmlBody}
        <div style="border-top:1px solid rgba(255,255,255,0.1);margin-top:30px;padding-top:20px;">
          <p style="color:#8a8680;font-size:12px;text-align:center;">Bossk Productions — Los Angeles, CA</p>
        </div>
      </div>
    </div>
  `;

  try {
    const beehiivRes = await fetch(`https://api.beehiiv.com/v2/publications/${PUB_ID}/posts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`
      },
      body: JSON.stringify({
        title: subject,
        subtitle: preview_text || '',
        status: 'confirmed',
        content_html: fullHtml
      })
    });

    const result = await beehiivRes.json();
    console.log('Beehiiv response:', beehiivRes.status, JSON.stringify(result).substring(0, 300));

    if (beehiivRes.ok) {
      return res.status(200).json({ success: true, id: result.data?.id });
    } else {
      return res.status(400).json({ error: result.message || 'Beehiiv error', detail: result });
    }
  } catch (e) {
    console.error('Beehiiv error:', e.message);
    return res.status(500).json({ error: e.message });
  }
}
