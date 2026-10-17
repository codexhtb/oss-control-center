/* Telegram T2 acceptance against a disposable PostgreSQL database. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function main() {
  if (!process.env.OSS_DISPOSABLE_DB?.startsWith('oss_test_phase55_browser_')) {
    throw new Error('Refusing mutations outside disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: false })
    const page = await context.newPage()
    await page.goto(origin)
    await page.locator('#username').fill('dev-engineer')
    await page.locator('#current-password').fill(users['dev-engineer'])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добро пожаловать|Добрый день/ }).waitFor()
    await page.goto(`${origin}/tickets`)
    await page.getByText('Синтетический ответ монтажника Telegram T2').click()
    await page.getByText('Выездные работы').click()
    await page.getByText('Монтажники сообщили о выполнении').waitFor()
    await page.getByText('Синтетический ответ: работы выполнены.').waitFor()
    const response = page.waitForResponse((item) =>
      item.url().endsWith('/verify') && item.request().method() === 'POST')
    await page.getByRole('button', { name: 'Подтвердить результат' }).click()
    if ((await response).status() !== 200) throw new Error('Engineer verification failed')
    await page.getByText('Результат подтверждён инженером').waitFor()
    console.log('Disposable Chromium: Telegram T2 replies and verification passed')
    await context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
