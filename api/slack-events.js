// Handles Slack Events API callbacks for the newsletter approval workflow.
// Reply "approve" (or similar) in the draft's Slack thread to send it.
// Reply with anything else to have Claude revise the draft based on your note.
//
// Requires SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET env vars.
// Uses raw body for signature verification, so auto body-parsing is disabled.

import crypto from 'crypto';

export const config = { api: { bodyParser: false } };

function getRawBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

const SHEET_URL = 'https://script.google.com/macros/s/AKfycbxEXavIf3HNYjEY16k28O3MnJv7WQLRwlFaPUDnMZKcsjnWrp3mjSydsU4mPA_UsbtP/exec';
const APPROVE_WORDS = ['approve', 'approved', 'yes', 'ship it', 'send it', 'looks good', 'lgtm', 'go ahead', 'send'];

export default async function handler(req, res) {
  const rawBody = await getRawBody(req);

  const signingSecret = process.env.SLACK_SIGNING_SECRET;
  if (signingSecret) {
    const timestamp = req.headers['x-slack-request-timestamp'];
    const slackSig = req.headers['x-slack-signature'];
    const baseString = `v0:${timestamp}:${rawBody}`;
    const mySig = 'v0=' + crypto.createHmac('sha256', signingSecret).update(baseString).digest('hex');
    const validTimestamp = timestamp && Math.abs(Date.now() / 1000 - timestamp) < 60 * 5;
    if (!validTimestamp || mySig !== slackSig) {
      return res.status(401).send('Invalid signature');
    }
  }

  let body;
  try {
    body = JSON.parse(rawBody);
  } catch (e) {
    return res.status(400).send('Bad request');
  }

  // Slack's one-time URL verification handshake when you turn on Event Subscriptions.
  if (body.type === 'url_verification') {
    return res.status(200).json({ challenge: body.challenge });
  }

  // Don't reprocess retries.
  if (req.headers['x-slack-retry-num']) {
    return res.status(200).send('ok');
  }

  // Ack immediately so Slack doesn't retry, then keep working.
  res.status(200).send('ok');

  if (body.type !== 'event_callback') return;
  const event = body.event;
  if (!event || event.type !== 'message') return;
  if (event.bot_id || event.subtype === 'bot_message') return;
  if (!event.thread_ts) return; // only care about threaded replies

  const BOT_TOKEN = process.env.SLACK_BOT_TOKEN;
  const threadKey = `${event.channel}|${event.thread_ts}`;

  try {
    // Find the pending draft this reply belongs to.
    const nlRes = await fetch(SHEET_URL + '?sheet=Newsletter', { redirect: 'follow' });
    const rows = await nlRes.json();
    const row = rows.find(r => (r['Slack Thread'] || '') === threadKey && (r.Status || '').toLowerCase() === 'pending_review');
    if (!row) return;

    const text = (event.text || '').trim().toLowerCase();
    const isApproval = APPROVE_WORDS.some(w => text.includes(w));

    if (isApproval) {
      const sendRes = await fetch('https://www.bosskproductions.com/api/newsletter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject: row.Subject,
          preview_text: row['Preview Text'],
          body: row.Body,
          cta_text: row['CTA Text'],
          cta_url: row['CTA URL'],
          header_image: row['Header Image'] || '',
          body_images: row['Body Images'] || '',
          video_url: row['Video URL'] || '',
          slack_thread: threadKey,
          action: 'publish'
        })
      });
      const sendResult = await sendRes.json();

      await postToSlack(BOT_TOKEN, event.channel, event.thread_ts,
        sendRes.ok
          ? `✅ Sent to ${sendResult.sent || 0} subscriber(s).`
          : `⚠️ Failed to send: ${sendResult.error || 'unknown error'}`
      );
      return;
    }

    // Treat as edit feedback — revise with Claude.
    const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
    if (!ANTHROPIC_KEY) return;

    const current = {
      subject_line: row.Subject,
      preview_text: row['Preview Text'],
      body: row.Body,
      cta_text: row['CTA Text'],
      cta_url: row['CTA URL']
    };

    const revisePrompt = `You are editing a draft newsletter for Bossk Productions. Here is the current draft as JSON:\n${JSON.stringify(current)}\n\nThe reviewer gave this feedback: "${event.text}"\n\nRevise the draft accordingly. Output ONLY valid JSON, same schema, no markdown fences, no preamble:\n{"subject_line":"...","preview_text":"...","body":"...","cta_text":"...","cta_url":"..."}`;

    const claudeRes = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1500,
        messages: [{ role: 'user', content: revisePrompt }]
      })
    });
    const claudeData = await claudeRes.json();
    const textBlocks = (claudeData.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
    const clean = textBlocks.replace(/```json|```/g, '').trim();
    const revised = JSON.parse(clean);

    // Save the revision back to the same row (matched by Slack Thread).
    await fetch(SHEET_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({
        _sheet: 'Newsletter',
        subject: revised.subject_line,
        preview_text: revised.preview_text,
        body: revised.body,
        cta_text: revised.cta_text,
        cta_url: revised.cta_url,
        header_image: row['Header Image'] || '',
        body_images: row['Body Images'] || '',
        video_url: row['Video URL'] || '',
        status: 'pending_review',
        slack_thread: threadKey
      }),
      redirect: 'follow'
    });

    await postToSlack(BOT_TOKEN, event.channel, event.thread_ts,
      `*Revised draft*\n*Subject:* ${revised.subject_line}\n*Preview:* ${revised.preview_text}\n\n${revised.body}\n\nReply "approve" to send, or give more edits.`
    );
  } catch (e) {
    console.error('Slack event handling error:', e.message);
  }
}

async function postToSlack(token, channel, thread_ts, text) {
  if (!token) return;
  await fetch('https://slack.com/api/chat.postMessage', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ channel, thread_ts, text })
  });
}
