import { chromium, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

const browser = await chromium.launch();
const results = [];
try {
  for (const width of [390, 1280]) {
    const pages = [];
    for (const [name, url] of [['reference', 'https://etforca.com/compose.html'], ['react', process.env.PORTFOLIO_URL || 'http://localhost:5180/portfolio/']]) {
      const page = await browser.newPage({ viewport: { width, height: 844 } });
      await page.goto(url);
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => Object.fromEntries(['.app', 'h2', '.acct', '.find', '.entry', '.askbox'].map(selector => {
        const element = document.querySelector(selector);
        const { x, y, width, height } = element.getBoundingClientRect();
        return [selector, { x, y, width, height, font: getComputedStyle(element).fontFamily }];
      })));
      await page.screenshot({ path: `test-results/${name}-${width}.png`, fullPage: true });
      pages.push(geometry);
      await page.close();
    }
    for (const selector of ['h2', '.acct', '.find', '.entry', '.askbox']) {
      // The requested default-cash donut shifts the account section down.
      for (const key of ['x', 'width']) expect(Math.abs(pages[0][selector][key] - pages[1][selector][key]), `${width} ${selector} ${key}`).toBeLessThan(3);
      expect(pages[1][selector].font).toEqual(pages[0][selector].font);
    }
    results.push({ width, geometry: pages, matched: true });
  }
  await writeFile('test-results/reference-comparison.json', JSON.stringify(results, null, 2));
  console.log('Reference input widths, alignment and fonts match at 390 and 1280 pixels.');
} finally { await browser.close(); }
