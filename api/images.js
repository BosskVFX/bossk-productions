export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const FOLDER_ID = '1p7JCwa6c3s5VcW40coM9kjUeEIpyxnt-';
  const CLIENT_EMAIL = 'bossk-website@bossk-website.iam.gserviceaccount.com';
  const PRIVATE_KEY_B64 = process.env.GOOGLE_DRIVE_KEY;

  if (!PRIVATE_KEY_B64) {
    return res.status(500).json({ error: 'GOOGLE_DRIVE_KEY not set' });
  }

  try {
    const crypto = await import('crypto');
    const PRIVATE_KEY = Buffer.from(PRIVATE_KEY_B64, 'base64').toString('utf-8');

    const now = Math.floor(Date.now() / 1000);
    const header = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
    const claims = Buffer.from(JSON.stringify({
      iss: CLIENT_EMAIL,
      scope: 'https://www.googleapis.com/auth/drive.readonly',
      aud: 'https://oauth2.googleapis.com/token',
      iat: now,
      exp: now + 3600
    })).toString('base64url');

    const signInput = header + '.' + claims;
    const sign = crypto.createSign('RSA-SHA256');
    sign.update(signInput);
    const signature = sign.sign(PRIVATE_KEY, 'base64url');
    const jwt = signInput + '.' + signature;

    const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`
    });
    const tokenData = await tokenRes.json();

    if (!tokenData.access_token) {
      return res.status(500).json({ error: 'Token failed', detail: tokenData });
    }

    const filesRes = await fetch(
      `https://www.googleapis.com/drive/v3/files?q='${FOLDER_ID}'+in+parents+and+mimeType+contains+'image/'&fields=files(id,name,mimeType,thumbnailLink)&pageSize=100`,
      { headers: { 'Authorization': `Bearer ${tokenData.access_token}` } }
    );
    const filesData = await filesRes.json();

    const images = (filesData.files || []).map(f => ({
      id: f.id,
      name: f.name,
      thumbnail: f.thumbnailLink || `https://lh3.googleusercontent.com/d/${f.id}=s400`,
      full: `https://lh3.googleusercontent.com/d/${f.id}`
    }));

    return res.status(200).json(images);

  } catch (e) {
    return res.status(500).json({ error: e.message, stack: e.stack });
  }
}
