export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const { name, email, message } = req.body;
  if (!message) return res.status(400).json({ error: 'Message is required' });

  const WEBHOOK = process.env.SLACK_WEBHOOK;

  const slackMsg = {
    blocks: [
      {
        type: 'header',
        text: { type: 'plain_text', text: '💬 New Website Message' }
      },
      {
        type: 'section',
        fields: [
          { type: 'mrkdwn', text: `*From:*\n${name || 'Anonymous'}` },
          { type: 'mrkdwn', text: `*Email:*\n${email || 'Not provided'}` }
        ]
      },
      {
        type: 'section',
        text: { type: 'mrkdwn', text: `*Message:*\n${message}` }
      },
      {
        type: 'context',
        elements: [
          { type: 'mrkdwn', text: `Via bosskproductions.com · ${new Date().toLocaleString('en-US', { timeZone: 'America/Los_Angeles' })}` }
        ]
      }
    ]
  };

  try {
    const r = await fetch(WEBHOOK, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(slackMsg)
    });
    if (r.ok) return res.status(200).json({ success: true });
    return res.status(500).json({ error: 'Slack error' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
