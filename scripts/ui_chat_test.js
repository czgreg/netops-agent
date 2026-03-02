const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(180000);
  page.on('console', msg => {
    console.log('[browser]', msg.type(), msg.text());
  });

  await page.goto('http://127.0.0.1:8787/', { waitUntil: 'domcontentloaded' });

  await page.waitForSelector('#q');
  await page.fill('#q', '拓扑同前，登录同前（host=10.160.6.126 telnet，R6=32774,R5=32773,R2=32770,R3=32771,R7=32775,R8=32776，admin/admin）。问题：6.6.6.6无法访问8.8.8.8。请从R6开始跨设备排障。');
  await page.click('button.primary');

  // Wait until at least one assistant message appears after the status bubble.
  await page.waitForTimeout(3000);
  let gotAssistant = true;
  try {
    await page.waitForFunction(() => {
      const msgs = Array.from(document.querySelectorAll('#chatMessages .msg-assistant'));
      return msgs.length > 0;
    }, { timeout: 140000 });
  } catch (_e) {
    gotAssistant = false;
  }

  const messages = await page.$$eval('#chatMessages .msg', nodes => nodes.map(n => n.textContent || ''));
  const details = await page.$eval('#details', n => n.textContent || '');
  const status = await page.$eval('#keyStatus', n => n.textContent || '');
  const modelInfo = await page.$eval('#modelInfo', n => n.textContent || '');

  const screenshotPath = path.resolve(__dirname, '../logs/ui-chat-test.png');
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log(JSON.stringify({ gotAssistant, status, modelInfo, messages: messages.slice(-8), details: details.slice(0, 1400) }, null, 2));

  await browser.close();
})();
