export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const FOLDER_ID = '1p7JCwa6c3s5VcW40coM9kjUeEIpyxnt-';
  const CLIENT_EMAIL = 'bossk-website@bossk-website.iam.gserviceaccount.com';
  const PRIVATE_KEY = process.env.GOOGLE_DRIVE_KEY;

  if (!PRIVATE_KEY) {
    return res.status(500).json({ error: 'GOOGLE_DRIVE_KEY not set' });
  }

  try {
    // Create JWT
    const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const now = Math.floor(Date.now() / 1000);
    const claims = btoa(JSON.stringify({
      iss: CLIENT_EMAIL,
      scope: 'https://www.googleapis.com/auth/drive.readonly',
      aud: 'https://oauth2.googleapis.com/token',
      iat: now,
      exp: now + 3600
    }));

    const signInput = header + '.' + claims;

    // Import the private key
    const keyData = PRIVATE_KEY.replace(/-----BEGIN PRIVATE KEY-----/g, '')
      .replace(/-----END PRIVATE KEY-----/g, '')
      .replace(/\\n/g, '')
      .replace(/\n/g, '')
      .trim();

    const binaryKey = Uint8Array.from(atob(keyData), c => c.charCodeAt(0));
    const cryptoKey = await crypto.subtle.importKey(
      'pkcs8', binaryKey,
      { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
      false, ['sign']
    );

    const signature = await crypto.subtle.sign(
      'RSASSA-PKCS1-v1_5',
      cryptoKey,
      new TextEncoder().encode(signInput)
    );

    const sig = btoa(String.fromCharCode(...new Uint8Array(signature)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');

    const jwt = signInput + '.' + sig;

    // Exchange JWT for access token
    const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`
    });
    const tokenData = await tokenRes.json();

    if (!tokenData.access_token) {
      console.error('Token error:', JSON.stringify(tokenData));
      return res.status(500).json({ error: 'Failed to get access token' });
    }

    // List files in folder
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

    res.setHeader('Cache-Control', 's-maxage=30, stale-while-revalidate');
    return res.status(200).json(images);

  } catch (e) {
    console.error('Drive API error:', e.message, e.stack);
    return res.status(500).json({ error: 'Failed to fetch images', detail: e.message });
  }
}
