const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [,, mode, out] = process.argv;
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  await p.goto('file://' + __dirname + '/video.html', { waitUntil: 'networkidle' });
  const data = JSON.parse(fs.readFileSync(__dirname + '/board.json'));
  await p.evaluate(d => window.__setup(d), data);
  const fonts = await p.evaluate(() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family + ' ' + f.weight));
  console.log('fonts', fonts.length, fonts.slice(0, 4));
  const canvas = await p.$('canvas');
  if (mode === 'keys') {
    for (const t of (process.argv[4] ? process.argv[4].split(',').map(Number) : [2.6, 7.5, 11.4, 14.5, 20.5, 25.2, 29.8, 35.5, 42.8, 46.5])) {
      await p.evaluate(t => window.__render(t), t);
      await canvas.screenshot({ path: `${out}/key_${String(t).replace('.', '_')}.png` });
    }
  } else {
    fs.mkdirSync(out, { recursive: true });
    const N = 67 * 30;
    for (let i = 0; i < N; i++) {
      await p.evaluate(t => window.__render(t), i / 30);
      await canvas.screenshot({ path: `${out}/f${String(i).padStart(5, '0')}.png` });
    }
  }
  await b.close();
})();
