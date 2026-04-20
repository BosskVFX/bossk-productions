export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const API_KEY = process.env.BEEHIIV_API_KEY;
  const PUB_ID = process.env.BEEHIIV_PUB_ID;

  if (!API_KEY || !PUB_ID) {
    return res.status(500).json({ error: 'Beehiiv credentials not set' });
  }

  const { subject, preview_text, body, cta_text, cta_url, header_image } = req.body;

  if (!subject || !body) {
    return res.status(400).json({ error: 'Subject and body are required' });
  }

  // Build HTML email content
  let htmlBody = '';
  
  if (header_image) {
    htmlBody += `<img src="${header_image}" alt="Newsletter header" style="width:100%;max-width:600px;height:auto;margin-bottom:20px;">`;
  }

  // Convert markdown-style body to HTML paragraphs
  const paragraphs = body.split('\n\n').filter(Boolean);
  htmlBody += paragraphs.map(p => {
    // Handle headers
    if (p.startsWith('### ')) return `<h3 style="color:#f0ede8;font-size:18px;margin:20px 0 8px;">${p.slice(4)}</h3>`;
    if (p.startsWith('## ')) return `<h2 style="color:#f0ede8;font-size:22px;margin:24px 0 10px;">${p.slice(3)}</h2>`;
    if (p.startsWith('# ')) return `<h1 style="color:#f0ede8;font-size:26px;margin:28px 0 12px;">${p.slice(2)}</h1>`;
    // Bold handling
    let text = p.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    return `<p style="color:#f0ede8;font-size:16px;line-height:1.7;margin:0 0 16px;">${text}</p>`;
  }).join('\n');

  if (cta_text && cta_url) {
    htmlBody += `<div style="text-align:center;margin:30px 0;"><a href="${cta_url}" style="background:#ff6b35;color:#0a0a0a;padding:14px 32px;text-decoration:none;font-weight:700;font-size:14px;letter-spacing:1px;text-transform:uppercase;display:inline-block;">${cta_text}</a></div>`;
  }

  // Wrap in email template
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
