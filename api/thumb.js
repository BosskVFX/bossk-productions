import sharp from 'sharp';

export default async function handler(req, res) {
  const { img } = req.query;
  
  if (!img) {
    return res.status(400).send('Missing img parameter');
  }

  try {
    // Fetch the original image
    const imgRes = await fetch(decodeURIComponent(img));
    const imgBuffer = Buffer.from(await imgRes.arrayBuffer());
    
    // Get image dimensions
    const metadata = await sharp(imgBuffer).metadata();
    const w = metadata.width || 600;
    const h = metadata.height || 400;
    
    // Create play button SVG overlay
    const circleSize = Math.min(w, h) * 0.18;
    const cx = w / 2;
    const cy = h / 2;
    const triSize = circleSize * 0.4;
    
    const overlaySvg = `
      <svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">
        <rect width="${w}" height="${h}" fill="rgba(0,0,0,0.15)"/>
        <circle cx="${cx}" cy="${cy}" r="${circleSize}" fill="rgba(255,107,53,0.92)"/>
        <polygon points="${cx - triSize * 0.4},${cy - triSize} ${cx + triSize},${cy} ${cx - triSize * 0.4},${cy + triSize}" fill="white"/>
      </svg>
    `;

    // Composite play button onto image
    const result = await sharp(imgBuffer)
      .composite([{
        input: Buffer.from(overlaySvg),
        blend: 'over'
      }])
      .jpeg({ quality: 85 })
      .toBuffer();

    res.setHeader('Content-Type', 'image/jpeg');
    res.setHeader('Cache-Control', 'public, max-age=86400');
    return res.status(200).send(result);
  } catch (e) {
    console.error('Thumb error:', e.message);
    return res.status(500).send('Failed to generate thumbnail');
  }
}
